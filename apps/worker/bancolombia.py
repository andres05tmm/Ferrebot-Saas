"""Jobs ARQ de la ingesta Bancolombia por Gmail: `procesar_gmail_push` (encolado por el webhook) y
`renovar_watch_gmail` (cron diario). La lógica determinista vive en `modules.bancos.gmail.ingesta`
(testeable con fakes); aquí solo el barrido/cableado multi-tenant (secretos, sesiones, Telegram, SSE).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from apps.bot.repos import ControlSecretosBot
from apps.bot.telegram import TelegramNotificador
from apps.tg_publico.repos import SecretosTgPublico
from core.config import get_settings
from core.db.session import control_session, tenant_session
from core.events.publisher import publish
from core.logging import get_logger
from core.tenancy.context import ResolvedTenant
from core.tenancy.control_repo import resolve_tenant_by_id
from modules.bancos.gmail.cliente import GmailCliente, RefreshTokenInvalido
from modules.bancos.gmail.ingesta import procesar_push
from modules.bancos.gmail.registro import RegistroGmail
from modules.bancos.gmail.secretos import guardar_refresh_token, leer_refresh_token
from modules.bancos.repository import SqlBancosRepository
from modules.pagos.conciliador_transferencias import conciliar_transferencia

log = get_logger("worker.bancolombia")

_MARGEN_RENOVAR = timedelta(hours=48)   # renueva el watch si expira dentro de esta ventana


async def _config_valor(cs, empresa_id: int, clave: str) -> str | None:
    row = (await cs.execute(
        text("SELECT valor FROM config_empresa WHERE empresa_id=:e AND clave=:c"),
        {"e": empresa_id, "c": clave},
    )).first()
    return row[0] if row else None


async def _construir_cliente(cs, empresa_id: int, cuenta) -> GmailCliente | None:
    """GmailCliente con las credenciales de plataforma (settings) + refresh_token del tenant (cifrado)."""
    s = get_settings()
    if not s.gmail_client_id or not s.gmail_client_secret:
        log.warning("gmail_sin_credenciales_plataforma")
        return None
    refresh = await leer_refresh_token(cs, s.secrets_master_key, empresa_id)
    if not refresh:
        log.warning("gmail_sin_refresh_token", empresa_id=empresa_id)
        return None
    return GmailCliente(
        client_id=s.gmail_client_id, client_secret=s.gmail_client_secret,
        refresh_token=refresh, usuario=cuenta.email or "me",
    )


async def _persistir_rotacion(cs, empresa_id: int, cliente: GmailCliente) -> None:
    if cliente.refresh_token_rotado:
        await guardar_refresh_token(
            cs, get_settings().secrets_master_key, empresa_id, cliente.refresh_token_rotado)


async def _alertar_telegram(cs, empresa_id: int, texto: str) -> None:
    """Notifica al grupo del tenant (bot_token cifrado + chat de config). Best-effort."""
    bot_token = await ControlSecretosBot(cs, get_settings().secrets_master_key).bot_token(empresa_id)
    chat = await _config_valor(cs, empresa_id, "telegram_notify_chat_id")
    if not bot_token or not chat:
        log.warning("gmail_sin_canal_telegram", empresa_id=empresa_id)
        return
    try:
        await TelegramNotificador(bot_token=bot_token).responder(int(chat), texto)
    except Exception:
        log.warning("gmail_telegram_fallo", empresa_id=empresa_id, exc_info=True)


async def procesar_gmail_push(ctx: dict, empresa_id: int, history_id: str | None) -> str:
    """Procesa el push de un tenant: lee los mensajes nuevos, persiste (idempotente por message_id),
    notifica a Telegram y emite SSE. Actualiza el `last_history_id` y re-cifra el token si Google lo rotó."""
    async with control_session() as cs:
        registro = RegistroGmail(cs)
        cuenta = await registro.por_empresa(empresa_id)
        if cuenta is None:
            return "sin_cuenta"
        cliente = await _construir_cliente(cs, empresa_id, cuenta)
        if cliente is None:
            return "sin_credenciales"
        tenant = await resolve_tenant_by_id(cs, empresa_id)
    if tenant is None:
        return "tenant_inexistente"

    bot_token = None
    chat = None
    async with control_session() as cs:
        bot_token = await ControlSecretosBot(cs, get_settings().secrets_master_key).bot_token(empresa_id)
        chat = await _config_valor(cs, empresa_id, "telegram_notify_chat_id")
        # Token del canal público (demo Sirius): para confirmarle el pago al cliente `tg:{chat_id}`.
        tg_token = await SecretosTgPublico(cs, get_settings().secrets_master_key).bot_token(empresa_id)

    async def notificar(texto: str) -> None:
        if bot_token and chat:
            await TelegramNotificador(bot_token=bot_token).responder(int(chat), texto)

    async def notificar_cliente(telefono: str, texto: str) -> None:
        # Canal Telegram público: teléfono opaco "tg:{chat_id}". WhatsApp (Kapso) queda para prod.
        if telefono.startswith("tg:") and tg_token:
            await TelegramNotificador(bot_token=tg_token).responder(int(telefono[3:]), texto)

    resultado = None
    try:
        async for s in tenant_session(tenant):     # commitea al cerrar el generador
            async def publicar(data: dict, _s=s) -> None:
                await publish(_s, "transferencia_recibida", data)

            async def al_insertar(mov, _s=s) -> None:
                # Puente transferencia → cobro → pedido pagado (plan demo Sirius §4): solo con
                # candidato único marca pagado, emite SSE `pedido_pagado` y notifica cliente/negocio.
                await conciliar_transferencia(
                    _s, monto=mov.monto,
                    notificar_cliente=notificar_cliente, notificar_negocio=notificar,
                )
            resultado = await procesar_push(
                cliente=cliente, repo=SqlBancosRepository(s),
                last_history_id=cuenta.last_history_id,
                notificar=notificar, publicar=publicar, al_insertar=al_insertar,
                history_id_push=history_id,
            )
    except RefreshTokenInvalido:
        async with control_session() as cs:
            await _alertar_telegram(
                cs, empresa_id,
                "⚠️ El acceso al correo de Bancolombia expiró. Vuelve a autorizar el buzón "
                "(tools/set_gmail_token) para seguir recibiendo las notificaciones de pagos.")
        return "refresh_invalido"

    async with control_session() as cs:
        if resultado and resultado.nuevo_history_id:
            await RegistroGmail(cs).guardar_history(empresa_id, resultado.nuevo_history_id)
        await _persistir_rotacion(cs, empresa_id, cliente)
    ins = resultado.insertados if resultado else 0
    return f"insertados={ins}"


async def poll_gmail_bancolombia(ctx: dict) -> str:
    """Cron por MINUTO: ingesta por POLLING para cuentas Gmail SIN Pub/Sub (demo Siriuss).

    El buzón puede tener su watch Pub/Sub apuntando a OTRO sistema (el legado de Punto Rojo, en
    producción): este poll solo LEE el historial con su propio refresh token — jamás llama
    `watch`, así que no desplaza el push del otro sistema. Reusa `procesar_gmail_push` completo
    (parser, idempotencia por gmail_message_id, conciliador de pedidos, SSE, notificaciones).
    Primera corrida de una cuenta (sin `last_history_id`): siembra la línea base con el
    historyId del perfil y sale — desde ahí solo se procesan correos NUEVOS.
    """
    async with control_session() as cs:
        cuentas = await RegistroGmail(cs).cuentas_activas()

    polleadas = 0
    for cuenta in cuentas:
        if cuenta.pubsub_topic:
            continue    # tiene push: su ingesta llega por webhook, no por poll
        try:
            if not cuenta.last_history_id:
                async with control_session() as cs:
                    cliente = await _construir_cliente(cs, cuenta.empresa_id, cuenta)
                    if cliente is None:
                        continue
                    base = await cliente.perfil_history_id()
                    if base:
                        await RegistroGmail(cs).guardar_history(cuenta.empresa_id, base)
                        await _persistir_rotacion(cs, cuenta.empresa_id, cliente)
                log.info("gmail_poll_baseline", empresa_id=cuenta.empresa_id)
                continue
            await procesar_gmail_push(ctx, cuenta.empresa_id, None)
            polleadas += 1
        except Exception:  # noqa: BLE001 — un buzón no debe tumbar el barrido
            log.exception("gmail_poll_error", empresa_id=cuenta.empresa_id)
    return f"polleadas={polleadas}"


async def renovar_watch_gmail(ctx: dict) -> str:
    """Cron diario: renueva el Gmail watch de cada cuenta activa cuyo watch expire dentro de 48h.

    El watch de Gmail caduca a los 7 días; renovarlo anticipadamente (y en cada corrida re-chequear)
    auto-repara fallos aislados. Un tenant que falle no tumba el barrido; si el refresh está revocado,
    se alerta a su grupo de Telegram."""
    ahora = datetime.now(timezone.utc)
    async with control_session() as cs:
        cuentas = await RegistroGmail(cs).cuentas_activas()

    renovadas = 0
    for cuenta in cuentas:
        if cuenta.watch_expira and cuenta.watch_expira > ahora + _MARGEN_RENOVAR:
            continue    # aún vigente con margen holgado
        if not cuenta.pubsub_topic:
            continue
        try:
            async with control_session() as cs:
                cliente = await _construir_cliente(cs, cuenta.empresa_id, cuenta)
                if cliente is None:
                    continue
                data = await cliente.watch(cuenta.pubsub_topic)
                expira = None
                if "expiration" in data:
                    try:
                        expira = datetime.fromtimestamp(int(data["expiration"]) / 1000, tz=timezone.utc)
                    except (ValueError, TypeError):
                        expira = None
                await RegistroGmail(cs).guardar_watch(
                    cuenta.empresa_id, history_id=data.get("historyId"), expira=expira)
                await _persistir_rotacion(cs, cuenta.empresa_id, cliente)
            renovadas += 1
            log.info("gmail_watch_renovado", empresa_id=cuenta.empresa_id, expira=str(expira))
        except RefreshTokenInvalido:
            async with control_session() as cs:
                await _alertar_telegram(
                    cs, cuenta.empresa_id,
                    "⚠️ No pude renovar el acceso al correo de Bancolombia (autorización expirada).")
        except Exception:  # noqa: BLE001 — un tenant no debe tumbar el barrido
            log.exception("gmail_watch_error", empresa_id=cuenta.empresa_id)
    return f"renovadas={renovadas}"
