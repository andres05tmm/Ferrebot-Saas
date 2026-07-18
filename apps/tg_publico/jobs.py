"""Job ARQ del canal Telegram público: atiende un mensaje con el runtime del agente de clientes.

Vive DENTRO de `apps.tg_publico` (no en `apps.worker.jobs`): es transporte de ESTE canal. Reusa el
mismo `AgenteWa` que WhatsApp (bucle LLM + herramientas por flag + memoria por cliente + handoff), solo
cambia el tubo de entrada/salida. El webhook encola; aquí el worker atiende.

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

import re
from pathlib import Path

from apps.tg_publico import sender as tg_sender
from apps.tg_publico.repos import SecretosTgPublico
from apps.wa.kapso import MensajeWa
from core.config import get_settings
from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.config_empresa import cargar_menu_foto_path

log = get_logger("tg_publico.jobs")

# Bypass de la FOTO del menú: si el cliente pide el menú, se manda la imagen configurada
# (config_empresa.menu_foto_path) ANTES del turno del agente — el texto del agente confirma
# precios/totales igual. Best-effort: sin foto/token, o con fallo de red, el turno sigue normal.
_RE_MENU = re.compile(r"men[uú]|carta|almuerzo", re.IGNORECASE)


async def _enviar_foto_menu(tenant_id: int, chat_id: int) -> bool:
    """Manda la foto del menú si el tenant la tiene configurada. True si se envió."""
    async with control_session() as cs:
        foto = await cargar_menu_foto_path(cs, tenant_id)
        token = await SecretosTgPublico(cs, get_settings().secrets_master_key).bot_token(tenant_id)
    if not foto or not token or not Path(foto).is_file():
        return False
    await tg_sender.enviar_foto(token, chat_id, foto)
    log.info("tg_menu_foto_enviada", tenant_id=tenant_id)
    return True


async def atender_mensaje_tg(
    ctx: dict, tenant_id: int, chat_id: int, texto: str, update_id: int
) -> str:
    """Atiende un mensaje de Telegram con el agente de clientes (encolado por el webhook)."""
    tenant = await ctx["resolver_tenant"](tenant_id)
    if tenant is None:
        log.warning("tg_publico_job_sin_tenant", tenant_id=tenant_id)
        return "sin_tenant"
    telefono = f"tg:{chat_id}"
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
