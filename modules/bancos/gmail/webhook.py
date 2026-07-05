"""Webhook del push de Gmail (Pub/Sub) — `POST /webhooks/bancolombia/{token}`.

Seguridad (espejo de `modules/facturacion/webhook.py`, tenancy.md §1): la empresa se resuelve por el
TOKEN opaco de la URL contra el control DB (jamás por el payload); token no registrado → 404
fail-closed. El push de Pub/Sub no trae firma HMAC — la protección ES el token secreto de la URL, que
fija la subscription. Responde <1s: decodifica el `historyId` y encola el trabajo real en el worker
(`procesar_gmail_push`); el fetch/parse/persist/notify NO ocurre en el request.

La ruta va FUERA de `/api/` (el TenantMiddleware la deja pasar sin JWT).
"""
from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from core.logging import get_logger, request_id_var, tenant_id_var

log = get_logger("bancos.gmail.webhook")


class AccionPush(str, Enum):
    NO_REGISTRADO = "no_registrado"
    BODY_INVALIDO = "body_invalido"
    ENCOLADO = "encolado"


@dataclass(frozen=True, slots=True)
class ResultadoPush:
    accion: AccionPush
    status: int


@dataclass(frozen=True, slots=True)
class WebhookGmailDeps:
    """Puertos (los implementa el wiring; los tests los falsean).

    - `resolver(token)` → empresa_id | None (control DB; None = token no registrado).
    - `encolar(empresa_id, history_id)` → encola `procesar_gmail_push` en el worker.
    """

    resolver: Callable[[str], Awaitable[int | None]]
    encolar: Callable[[int, str | None], Awaitable[None]]


def _history_id_de_push(cuerpo: bytes) -> str | None:
    """historyId del envelope de Pub/Sub: {message:{data: base64({emailAddress, historyId})}}."""
    try:
        envelope = json.loads(cuerpo)
        data_b64 = envelope.get("message", {}).get("data", "")
        if not data_b64:
            return None
        interno = json.loads(base64.urlsafe_b64decode(data_b64 + "==").decode("utf-8"))
        hid = interno.get("historyId")
        return str(hid) if hid is not None else None
    except (ValueError, UnicodeDecodeError, TypeError):
        return None


async def manejar_push(
    *, token: str, cuerpo: bytes, deps: WebhookGmailDeps, request_id: str | None = None,
) -> ResultadoPush:
    """Orquesta un push entrante. Toda la tenancy; sin FastAPI (testeable). Siempre 200 tras encolar."""
    rid = request_id or uuid.uuid4().hex
    request_id_var.set(rid)
    tenant_id_var.set(None)

    empresa_id = await deps.resolver(token)
    if empresa_id is None:
        log.warning("gmail_push_no_registrado")
        return ResultadoPush(AccionPush.NO_REGISTRADO, 404)
    tenant_id_var.set(empresa_id)

    # El historyId es informativo (el worker relee desde last_history_id): si no se pudo decodificar,
    # igual encolamos — el worker toma el rango desde el último punto guardado. Nunca fallar el push
    # por un envelope raro (Pub/Sub reintentaría en bucle).
    history_id = _history_id_de_push(cuerpo)
    await deps.encolar(empresa_id, history_id)
    log.info("gmail_push_encolado", history_id=history_id)
    return ResultadoPush(AccionPush.ENCOLADO, 200)


def crear_router_bancolombia():
    """Ruta `POST /webhooks/bancolombia/{token}`. Toma las deps de `app.state` (import perezoso de
    FastAPI para no cargarlo en el worker/tests que solo usan la lógica pura)."""
    from fastapi import APIRouter
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    router = APIRouter(tags=["bancos-webhook"])

    @router.post("/webhooks/bancolombia/{token}")
    async def bancolombia_push(token: str, request: Request) -> JSONResponse:
        cuerpo = await request.body()
        deps: WebhookGmailDeps = request.app.state.bancolombia_webhook_deps
        rid = request.headers.get("x-request-id")
        res = await manejar_push(token=token, cuerpo=cuerpo, deps=deps, request_id=rid)
        return JSONResponse({"accion": res.accion.value}, status_code=res.status)

    return router
