"""Webhook del canal Telegram público del agente de clientes: `POST /tg-publico/{slug}`.

Espejo de `apps/wa/webhook.py` (transporte + tenancy) y de `apps/bot/webhook.py` (secret-token
fail-closed en tiempo constante). Orden NO negociable (security.md / tenancy.md §1):

  1. Resolver la empresa por el slug (control DB). Slug sin empresa → 200 SIN abrir la base (un 5xx
     dispararía retry-storm de Telegram; con 200 no reintenta). Empresa inactiva → 200 SIN procesar.
     (Espeja apps/wa: número no mapeado/inactivo → 200, no 404/403.)
  2. Validar el `X-Telegram-Bot-Api-Secret-Token` en TIEMPO CONSTANTE, fail-closed, ANTES de tocar la
     base del tenant → 403. Empresa sin secret o header ausente/distinto → 403.
  3. Parsear el update; solo mensaje de TEXTO en chat PRIVADO. Cualquier otra cosa se ignora (200).
  4. Dedup por `(tenant, update_id)` en Redis: un reintento del webhook no se procesa dos veces (200).
  5. `Contexto` público (`cliente_telefono = "tg:{chat_id}"`, SIEMPRE del payload — jamás del modelo)
     y encolar el turno en ARQ (job `atender_mensaje_tg`).

Responde 200 rápido; el turno del agente va encolado (`deps.procesar` no bloquea el webhook). El parseo
del update es propio (no depende de `python-telegram-bot`), igual que `apps.bot.webhook`.
"""
from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import JSONResponse

from ai.envelope import Contexto
from apps.tg_publico.ports import AccionTg, ResultadoTg, TgPublicoDeps, UpdateTgPublico
from core.logging import get_logger, request_id_var, tenant_id_var

log = get_logger("tg_publico.webhook")


def parsear_update_tg(payload: dict) -> UpdateTgPublico | None:
    """Extrae lo mínimo de un update. None si no es un mensaje de TEXTO en chat PRIVADO.

    El canal público solo atiende texto en privado: grupos/canales, ediciones, callbacks, fotos, voz,
    etc. se ignoran (200). Un update sin `update_id`, sin `chat.id` o con texto vacío no es procesable.
    """
    mensaje = payload.get("message")
    if not isinstance(mensaje, dict):
        return None
    chat = mensaje.get("chat") or {}
    if chat.get("type") != "private":
        return None
    update_id = payload.get("update_id")
    chat_id = chat.get("id")
    texto = mensaje.get("text")
    if update_id is None or chat_id is None or not isinstance(texto, str) or not texto:
        return None
    return UpdateTgPublico(update_id=int(update_id), chat_id=int(chat_id), texto=texto)


def _secret_valido(configurado: str | None, provisto: str | None) -> bool:
    """Comparación en tiempo constante del secret-token. Fail-closed: si falta cualquiera de los dos
    (empresa sin secret o header ausente), NO valida."""
    if not configurado or not provisto:
        return False
    return hmac.compare_digest(configurado, provisto)


async def manejar_update_tg(
    slug: str,
    secret_token: str | None,
    payload: dict,
    deps: TgPublicoDeps,
    *,
    request_id: str | None = None,
) -> ResultadoTg:
    """Orquesta un update. Toda la seguridad y la tenancy; sin FastAPI ni red (testeable con fakes)."""
    rid = request_id or uuid.uuid4().hex
    rid_token = request_id_var.set(rid)
    tid_token = tenant_id_var.set(None)
    try:
        # 1. Resolver la empresa por slug. No mapeada/inactiva → 200 sin procesar (espeja apps/wa).
        tenant = await deps.resolver.por_slug(slug)
        if tenant is None:
            log.info("tg_publico_slug_no_mapeado", slug=slug)
            return ResultadoTg(AccionTg.NO_MAPEADO, 200)
        if tenant.estado != "activa":
            tenant_id_var.set(tenant.id)
            log.info("tg_publico_empresa_inactiva", slug=slug, estado=tenant.estado)
            return ResultadoTg(AccionTg.EMPRESA_INACTIVA, 200)
        tenant_id_var.set(tenant.id)

        # 2. Validar el secret-token (fail-closed, tiempo constante) ANTES de tocar la base del tenant.
        configurado = await deps.secretos.webhook_secret(tenant.id)
        if not _secret_valido(configurado, secret_token):
            log.warning("tg_publico_secret_invalido", slug=slug)
            return ResultadoTg(AccionTg.SECRET_INVALIDO, 403)

        # 3. Parsear (solo texto en privado); ignorar lo demás.
        update = parsear_update_tg(payload)
        if update is None:
            log.info("tg_publico_update_ignorado")
            return ResultadoTg(AccionTg.UPDATE_IGNORADO, 200)

        # 4. Dedup por (tenant, update_id) — reintentos del webhook.
        if not await deps.dedup.marcar_si_nuevo(tenant.id, update.update_id):
            log.info("tg_publico_update_duplicado", update_id=update.update_id)
            return ResultadoTg(AccionTg.DUPLICADO, 200)

        # 5. Contexto público: cliente_telefono SIEMPRE del payload. Encolar (no bloquea).
        ctx = Contexto(
            tenant_id=tenant.id,
            usuario_id=0,                       # canal público: no hay usuario de staff
            rol="cliente",
            origen="telegram",
            cliente_telefono=f"tg:{update.chat_id}",
            request_id=rid,
        )
        try:
            await deps.procesar(update, ctx)
        except Exception:
            # Encolado fallido: se DESMARCA el dedup para que el reintento de Telegram sí procese
            # (sin esto el reintento caería como duplicado y el mensaje se perdería para siempre).
            log.exception("tg_publico_encolado_error", update_id=update.update_id)
            await deps.dedup.desmarcar(tenant.id, update.update_id)
            raise
        log.info("tg_publico_update_procesado", update_id=update.update_id)
        return ResultadoTg(AccionTg.PROCESADO, 200, ctx)
    finally:
        request_id_var.reset(rid_token)
        tenant_id_var.reset(tid_token)


def crear_router_tg_publico() -> APIRouter:
    """Ruta `POST /tg-publico/{slug}`. Toma las deps de `app.state.tg_deps` (montado FUERA de /api/)."""
    router = APIRouter(tags=["telegram-publico"])

    @router.post("/tg-publico/{slug}")
    async def tg_publico_webhook(slug: str, request: Request) -> JSONResponse:
        secret = request.headers.get("x-telegram-bot-api-secret-token")
        rid = request.headers.get("x-request-id")
        try:
            payload = await request.json()
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"detail": "cuerpo JSON inválido"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"detail": "cuerpo JSON inválido"}, status_code=400)
        deps: TgPublicoDeps = request.app.state.tg_deps
        res = await manejar_update_tg(slug, secret, payload, deps, request_id=rid)
        return JSONResponse({"accion": res.accion.value}, status_code=res.status)

    return router
