"""Webhook del bot: `POST /tg/{slug}`.

Flujo (orden NO negociable, tenancy.md §1 / security.md):

  1. Resolver la empresa por el slug (control DB + caché). Si no existe → 404; inactiva → 403.
  2. Validar el `X-Telegram-Bot-Api-Secret-Token` **en tiempo constante** contra el secret cifrado
     de la empresa. Ausente, no configurado o distinto → 403, **sin abrir la base del tenant**.
     *Fail-closed:* una empresa sin secret configurado se rechaza (a diferencia del FerreBot
     original, que era fail-open con `AUTHORIZED_CHAT_IDS` vacío).
  3. Parsear el update; si no es un mensaje procesable → se ignora (200, sin tocar el tenant).
  4. Dedup por `update_id` (Redis): un reintento del webhook no se procesa dos veces.
  5. Abrir la sesión del tenant; mapear `telegram_id` → usuario activo. No mapeado/inactivo →
     "no autorizado" (sin mutar).
  6. Armar `Contexto` (tenant, usuario, rol, capacidades, idempotency_key determinista por
     `(tenant, update_id)`, origen=bot) y delegar el turno a `deps.procesar`.

El parseo del update es propio (no se depende de `python-telegram-bot`): un bot-token por empresa,
N empresas, sin una `Application` viva por tenant (checkpoint C1). La confirmación R3 (entregable 3)
NO usa la derivación de `idempotency_key` de aquí: reusa la key guardada del turno pendiente.
"""
from __future__ import annotations

import hmac
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ai.envelope import Contexto
from apps.bot.ports import Accion, BotDeps, CallbackBot, ResultadoWebhook, UpdateBot
from core.logging import get_logger, request_id_var, tenant_id_var

log = get_logger("bot.webhook")

# Namespace fijo para derivar idempotency_key determinista por (tenant, update_id).
_NS_TELEGRAM = uuid.UUID("8f3b1c2a-0d4e-4a6b-9c8d-1e2f3a4b5c6d")

_MSG_NO_AUTORIZADO = "No estás autorizado para operar este bot. Pide acceso al administrador."


def parsear_update(payload: dict) -> UpdateBot | CallbackBot | None:
    """Extrae lo mínimo de un update de Telegram. None si no es procesable. Soporta texto, nota de
    voz y `callback_query` (pulsación de botón inline)."""
    if isinstance(payload.get("callback_query"), dict):
        return _parsear_callback(payload["callback_query"])
    mensaje = payload.get("message")
    if not isinstance(mensaje, dict):
        return None
    update_id = payload.get("update_id")
    chat_id = (mensaje.get("chat") or {}).get("id")
    telegram_id = (mensaje.get("from") or {}).get("id")
    if update_id is None or chat_id is None or telegram_id is None:
        return None
    texto = mensaje.get("text")
    voz_file_id = (mensaje.get("voice") or {}).get("file_id")
    if texto is None and voz_file_id is None:
        return None  # ni texto ni voz: nada que procesar
    return UpdateBot(
        update_id=int(update_id), chat_id=int(chat_id), telegram_id=int(telegram_id),
        texto=texto, voz_file_id=voz_file_id,
    )


def _parsear_callback(cb: dict) -> CallbackBot | None:
    """Extrae lo mínimo de un `callback_query` → `CallbackBot`. None si falta algún campo clave
    (`id`, `from.id`, `message.chat.id`, `data`): un callback sin destino o sin acción se ignora."""
    callback_id = cb.get("id")
    telegram_id = (cb.get("from") or {}).get("id")
    chat_id = ((cb.get("message") or {}).get("chat") or {}).get("id")
    data = cb.get("data")
    if callback_id is None or telegram_id is None or chat_id is None or data is None:
        return None
    return CallbackBot(
        callback_id=str(callback_id), chat_id=int(chat_id),
        telegram_id=int(telegram_id), data=str(data),
    )


def clave_idempotencia(tenant_id: int, update_id: int) -> str:
    """`idempotency_key` determinista por `(empresa, update_id)`: un reintento del webhook de
    Telegram reusa la misma key, así el servicio de dominio dedup-ea aunque falle el dedup de Redis.
    Incluye el tenant para que el update_id de dos empresas no colisione."""
    return str(uuid.uuid5(_NS_TELEGRAM, f"{tenant_id}:{update_id}"))


def _secret_valido(configurado: str | None, provisto: str | None) -> bool:
    """Comparación en tiempo constante del secret-token. Fail-closed: si falta cualquiera de los
    dos (empresa sin secret o header ausente), NO valida."""
    if not configurado or not provisto:
        return False
    return hmac.compare_digest(configurado, provisto)


async def manejar_update(
    slug: str,
    secret_token: str | None,
    payload: dict,
    deps: BotDeps,
    *,
    request_id: str | None = None,
) -> ResultadoWebhook:
    """Orquesta un update. Toda la lógica de seguridad y tenancy; sin FastAPI ni red (testeable)."""
    rid = request_id or uuid.uuid4().hex
    rid_token = request_id_var.set(rid)
    tid_token = tenant_id_var.set(None)
    try:
        # 1. Resolver la empresa.
        tenant = await deps.resolver.por_slug(slug)
        if tenant is None:
            log.info("bot_empresa_no_encontrada", slug=slug)
            return ResultadoWebhook(Accion.EMPRESA_NO_ENCONTRADA, 404)
        if tenant.estado != "activa":
            log.info("bot_empresa_inactiva", slug=slug, estado=tenant.estado)
            return ResultadoWebhook(Accion.EMPRESA_INACTIVA, 403)
        tenant_id_var.set(tenant.id)

        # 2. Validar el secret-token (fail-closed, tiempo constante) ANTES de tocar la base.
        configurado = await deps.secretos.webhook_secret(tenant.id)
        if not _secret_valido(configurado, secret_token):
            log.warning("bot_secret_invalido", slug=slug)
            return ResultadoWebhook(Accion.SECRET_INVALIDO, 403)

        # Recursos de ESTA empresa (notificador atado a su bot-token; cacheado por empresa).
        bundle = await deps.recursos.para(tenant.id)

        # 3. Parsear (mensaje o callback); ignorar lo que no es procesable.
        update = parsear_update(payload)
        if update is None:
            log.info("bot_update_ignorado")
            return ResultadoWebhook(Accion.UPDATE_IGNORADO, 200)

        # 4. Dedup por update_id (reintentos del webhook). El update_id viaja en el nivel superior;
        #    para un mensaje coincide con `update.update_id`, para un callback se toma del payload.
        update_id = update.update_id if isinstance(update, UpdateBot) else int(payload["update_id"])
        if not await deps.dedup.marcar_si_nuevo(tenant.id, update_id):
            log.info("bot_update_duplicado", update_id=update_id)
            return ResultadoWebhook(Accion.DUPLICADO, 200)

        # 5. Sesión del tenant + mapeo del usuario (idéntico para mensaje y callback).
        async with deps.abrir_sesion(tenant) as session:
            usuario = await deps.usuarios(session).por_telegram_id(update.telegram_id)
            if usuario is None or not usuario.activo:
                await bundle.notificador.responder(update.chat_id, _MSG_NO_AUTORIZADO)
                log.info("bot_no_autorizado", telegram_id=update.telegram_id)
                return ResultadoWebhook(Accion.NO_AUTORIZADO, 200)

            # 6. Contexto + delegación. Un callback va a `procesar_callback`; un mensaje, al turno.
            ctx = Contexto(
                tenant_id=tenant.id,
                usuario_id=usuario.id,
                rol=usuario.rol,
                origen="bot",
                idempotency_key=clave_idempotencia(tenant.id, update_id),
                request_id=rid,
                capacidades=await deps.capacidades.efectivas(tenant.id),
                # Rubro del negocio (persona del prompt); None = fallback ferretero histórico.
                rubro=(await deps.rubro.rubro(tenant.id)) if deps.rubro is not None else None,
            )
            if isinstance(update, CallbackBot):
                if deps.procesar_callback is None:
                    log.info("bot_callback_sin_handler", update_id=update_id)
                    return ResultadoWebhook(Accion.UPDATE_IGNORADO, 200)
                await deps.procesar_callback(update, ctx, session, bundle.notificador)
                log.info("bot_callback_procesado", usuario_id=usuario.id, update_id=update_id)
                return ResultadoWebhook(Accion.PROCESADO, 200, ctx)

            await deps.procesar(update, ctx, session, bundle.notificador)
            log.info("bot_turno_procesado", usuario_id=usuario.id, update_id=update_id)
            return ResultadoWebhook(Accion.PROCESADO, 200, ctx)
    finally:
        request_id_var.reset(rid_token)
        tenant_id_var.reset(tid_token)


def crear_app_bot(deps: BotDeps) -> FastAPI:
    """App ASGI del servicio bot: una ruta `POST /tg/{slug}`. Mapea `ResultadoWebhook` → HTTP."""
    app = FastAPI(title="FerreBot SaaS Bot", version="0.1.0")

    @app.post("/tg/{slug}")
    async def webhook(slug: str, request: Request) -> JSONResponse:
        secret = request.headers.get("x-telegram-bot-api-secret-token")
        rid = request.headers.get("x-request-id")
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            # Body inválido: superficie pública del webhook → 400, no 500.
            return JSONResponse({"detail": "cuerpo JSON inválido"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"detail": "cuerpo JSON inválido"}, status_code=400)
        res = await manejar_update(slug, secret, payload, deps, request_id=rid)
        return JSONResponse({"accion": res.accion.value}, status_code=res.status)

    @app.api_route("/health", methods=["GET", "HEAD"], tags=["infra"])
    async def health() -> dict:
        return {"status": "ok"}

    return app
