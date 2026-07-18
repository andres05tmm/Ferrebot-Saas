"""Job ARQ del canal Telegram público: atiende un mensaje con el runtime del agente de clientes.

Vive DENTRO de `apps.tg_publico` (no en `apps.worker.jobs`): es transporte de ESTE canal. Reusa el
mismo `AgenteWa` que WhatsApp (bucle LLM + herramientas por flag + memoria por cliente + handoff), solo
cambia el tubo de entrada/salida. El webhook encola; aquí el worker atiende.

Dos caminos según el update: TEXTO → turno del agente; FOTO (`foto_file_id`) → comprobante de pago
(visión `extraer_recibo` + asociación a cobro del frente B `registrar_comprobante`), sin correr el LLM
de texto y confirmando SIEMPRE al cliente (el pago es operativo, no conversación). Ver `_atender_comprobante`.

Molde exacto de `apps.worker.jobs.atender_mensaje_wa`:
  - Seams inyectados por `on_startup`: `ctx["resolver_tenant"]` (tenant_id → ResolvedTenant, YA existe y
    se comparte con el canal WhatsApp) y `ctx["tg_agente"]` (el `AgenteWa` con sender de Telegram, que
    construye `apps.tg_publico.wiring.construir_agente_tg`).
  - El turno se SERIALIZA por conversación con un lock Redis por `(tenant, teléfono)`: dos mensajes del
    mismo cliente en paralelo harían GET→append→SET sobre la memoria y el último SET pisaría el otro.
    Sin `ctx["redis"]` (tests/smoke) no hay lock.

La identidad del cliente (`telefono = "tg:{chat_id}"`) SALE DEL PAYLOAD del webhook, nunca del modelo.
El handoff (si `esta_en_humano(telefono)`: no correr el agente, solo persistir el entrante) lo aplica
`AgenteWa.atender` internamente — el mismo camino que WhatsApp.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from ai.vision.recibo import ReciboExtraido, extraer_recibo
from apps.tg_publico import sender as tg_sender
from apps.tg_publico.repos import SecretosTgPublico
from apps.tg_publico.sender import TelegramPublicoSender
from apps.wa.kapso import MensajeWa
from core.config import get_settings
from core.db.session import control_session, tenant_session
from core.llm.base import ImageBlock
from core.logging import get_logger
from core.tenancy.config_empresa import cargar_menu_foto_path
from core.tenancy.context import ResolvedTenant
from modules.conversaciones.repository import SqlConversacionRepository

log = get_logger("tg_publico.jobs")

# Mensaje entrante que se persiste en el hilo del inbox por una foto de comprobante (el binario no
# se guarda como texto). Se le anexa el caption si el cliente escribió algo con la foto.
_ENTRANTE_COMPROBANTE = "[📎 comprobante de pago]"
# Respuesta amable fija si la visión/descarga falla: el comprobante NO se pierde, un humano lo revisa.
_MSG_ILEGIBLE = (
    "Recibí tu comprobante 🙏 pero no pude leerlo bien; un asesor lo revisa en un momento"
)

# Bypass de la FOTO del menú: si el cliente pide el menú, se manda la imagen configurada
# (config_empresa.menu_foto_path) ANTES del turno del agente — el texto del agente confirma
# precios/totales igual. Best-effort: sin foto/token, o con fallo de red, el turno sigue normal.
_RE_MENU = re.compile(r"men[uú]|carta|almuerzo", re.IGNORECASE)


async def _enviar_foto_menu(tenant_id: int, chat_id: int) -> bool:
    """Manda la foto del menú si el tenant la tiene configurada. True si se envió."""
    async with control_session() as cs:
        foto = await cargar_menu_foto_path(cs, tenant_id)
        token = await SecretosTgPublico(cs, get_settings().secrets_master_key).bot_token(tenant_id)
    es_url = bool(foto) and foto.startswith(("http://", "https://"))
    if not foto or not token or (not es_url and not Path(foto).is_file()):
        return False
    await tg_sender.enviar_foto(token, chat_id, foto)
    log.info("tg_menu_foto_enviada", tenant_id=tenant_id)
    return True


async def atender_mensaje_tg(
    ctx: dict,
    tenant_id: int,
    chat_id: int,
    texto: str,
    update_id: int,
    foto_file_id: str | None = None,
) -> str:
    """Atiende un mensaje de Telegram (encolado por el webhook).

    Texto → turno del agente de clientes (bucle LLM). FOTO (`foto_file_id`) → camino comprobante de
    pago: se lee con visión y se asocia a un cobro, sin correr el LLM de texto (el bypass del menú,
    que es solo para texto, no aplica).
    """
    tenant = await ctx["resolver_tenant"](tenant_id)
    if tenant is None:
        log.warning("tg_publico_job_sin_tenant", tenant_id=tenant_id)
        return "sin_tenant"
    telefono = f"tg:{chat_id}"
    if foto_file_id:
        await _atender_comprobante(tenant, telefono, texto, foto_file_id)
        return "comprobante"
    if _RE_MENU.search(texto):
        try:
            await _enviar_foto_menu(tenant.id, chat_id)
        except Exception:  # noqa: BLE001 — la foto es cortesía; nunca tumba el turno
            log.warning("tg_menu_foto_error", tenant_id=tenant_id, exc_info=True)
    # `phone_number_id` transporta el tenant_id (el sender de Telegram lo usa para resolver el token
    # por tenant); `message_id` no lo usa el agente — el dedup ya ocurrió en el webhook por update_id.
    mensaje = MensajeWa(
        message_id=f"tg:{tenant_id}:{update_id}",
        telefono=telefono,
        phone_number_id=str(tenant_id),
        texto=texto,
    )
    redis = ctx.get("redis")
    if redis is None:
        await ctx["tg_agente"].atender(mensaje, tenant)
        return "atendido"
    lock = redis.lock(f"tg:conv:lock:{tenant_id}:{telefono}", timeout=180, blocking_timeout=120)
    async with lock:
        await ctx["tg_agente"].atender(mensaje, tenant)
    return "atendido"


# ── Camino FOTO: comprobante de pago del cliente ──────────────────────────────
#
# Best-effort en TODO (regla del brief): un fallo de descarga/visión responde `_MSG_ILEGIBLE` y NO
# tumba el job. La confirmación de pago es OPERATIVA (no conversación): se responde y se registra el
# comprobante aunque la conversación esté en manos de un humano. La foto de un comprobante NO dispara
# el bypass de la foto del menú (ese es solo para texto: aquí ni se evalúa `_RE_MENU`).


async def _atender_comprobante(
    tenant: ResolvedTenant, telefono: str, caption: str, file_id: str
) -> None:
    """Lee el comprobante con visión, lo asocia a un cobro (frente B) y confirma al cliente."""
    datos = await _extraer_comprobante(tenant.id, file_id)  # None si la visión/descarga falló

    mensaje_cliente = _MSG_ILEGIBLE
    entrante = _ENTRANTE_COMPROBANTE + (f"\n{caption}" if caption else "")
    try:
        async for session in tenant_session(tenant):
            repo = SqlConversacionRepository(session)
            await repo.asegurar(telefono)
            await repo.agregar_mensaje(telefono, "entrante", "cliente", entrante)
            if datos is not None:
                try:
                    registrar = _registrar_comprobante()
                    resultado = await registrar(
                        session, cliente_telefono=telefono, datos=datos, imagen_ref=file_id
                    )
                    mensaje_cliente = resultado.mensaje_cliente
                except Exception:  # noqa: BLE001 — el frente B falló; caemos al mensaje amable fijo
                    log.warning("tg_comprobante_registro_error", tenant_id=tenant.id, exc_info=True)
            await repo.agregar_mensaje(telefono, "saliente", "bot", mensaje_cliente)
    except Exception:  # noqa: BLE001 — persistir es best-effort; igual respondemos al cliente
        log.warning("tg_comprobante_persist_error", tenant_id=tenant.id, exc_info=True)

    try:
        await _responder(tenant.id, telefono, mensaje_cliente)
    except Exception:  # noqa: BLE001 — el envío es best-effort; nunca tumba el job
        log.warning("tg_comprobante_envio_error", tenant_id=tenant.id, exc_info=True)


async def _extraer_comprobante(tenant_id: int, file_id: str) -> ReciboExtraido | None:
    """Descarga la foto y la lee con la visión del tenant. None ante cualquier fallo (best-effort)."""
    try:
        data = await _descargar_foto_tg(tenant_id, file_id)
        imagen = ImageBlock.desde_base64(base64.b64encode(data).decode("ascii"), "image/jpeg")
        llm = await _resolver_vision(tenant_id)
        return await extraer_recibo(imagen, llm.provider, modelo=llm.model)
    except Exception:  # noqa: BLE001 — descarga/visión/red: se degrada a mensaje amable
        log.warning("tg_comprobante_vision_error", tenant_id=tenant_id, exc_info=True)
        return None


async def _descargar_foto_tg(tenant_id: int, file_id: str) -> bytes:
    """Bytes de la foto vía la Bot API con el token cifrado del tenant (clave tg_publico_bot_token)."""
    from apps.bot.telegram import TelegramArchivos

    async with control_session() as cs:
        token = await SecretosTgPublico(cs, get_settings().secrets_master_key).bot_token(tenant_id)
    if not token:
        raise RuntimeError(f"tenant {tenant_id} sin tg_publico_bot_token")
    return await TelegramArchivos(bot_token=token).descargar(file_id)


async def _resolver_vision(tenant_id: int):
    """(Proveedor + modelo) con visión del tenant. Import perezoso: `wiring` arrastra el AgenteWa."""
    from apps.tg_publico.wiring import resolver_vision_tg

    return await resolver_vision_tg(tenant_id)


def _registrar_comprobante():
    """Función del frente B (`modules/pagos/comprobantes.py`). Import perezoso: aún no aterriza y los
    tests monkeypatchean este seam."""
    from modules.pagos.comprobantes import registrar_comprobante

    return registrar_comprobante


_sender_tg: TelegramPublicoSender | None = None


async def _responder(tenant_id: int, telefono: str, texto: str) -> None:
    """Responde al cliente por Telegram (sender por-tenant, token cacheado; interfaz `KapsoSender`)."""
    global _sender_tg
    if _sender_tg is None:
        _sender_tg = TelegramPublicoSender(get_settings().secrets_master_key)
    await _sender_tg.enviar_texto(phone_number_id=str(tenant_id), to=telefono, texto=texto)
