"""Webhook de MATIAS: `POST /webhooks/matias/{token}` (prerrequisito D7.1 del ADR 0012).

Orden de seguridad NO negociable (espejo de `apps/wa/webhook.py`, security.md / tenancy.md §1):

  1. Resolver la empresa por el TOKEN del registro (control DB), NUNCA por el payload. No registrado → 404.
  2. Verificar la firma HMAC-SHA256 (`X-Webhook-Signature`) sobre el cuerpo CRUDO con el secret CIFRADO
     de la empresa (`secretos_empresa`). Fail-closed → 401 si no valida.
  3. Exigir `X-Webhook-ID` (idempotencia) → 400 si falta.
  4. Parsear el cuerpo (JSON) → 400 si no es un objeto.
  5. Idempotencia: registrar el `X-Webhook-ID` en la base del tenant (UNIQUE). Ya visto → 200 (duplicado).
  6. Encolar el procesamiento en el worker (no bloquea: responde <5s) → 200.

El cambio de estado de la factura (`document.accepted/rejected/voided`) + el evento SSE ocurren en el
worker (`apps.worker.jobs.procesar_webhook_matias`), no en el request. La ruta NO va bajo `/api/`: el
TenantMiddleware la deja pasar sin JWT; la protección es la firma + el token del registro.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.logging import get_logger, request_id_var, tenant_id_var

log = get_logger("facturacion.webhook")


def verificar_firma_matias(secret: str | None, cuerpo: bytes, firma: str | None) -> bool:
    """Valida la firma HMAC-SHA256 del webhook de MATIAS en tiempo constante. Fail-closed.

    Sin secret configurado o sin header de firma → NO valida (no se procesa nada sin firma válida).
    Tolera el prefijo `sha256=` (convención común de webhooks) además del hex pelado."""
    if not secret or not firma:
        return False
    recibido = firma.strip()
    if recibido.lower().startswith("sha256="):
        recibido = recibido[7:]
    esperado = hmac.new(secret.encode("utf-8"), cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, recibido)


class AccionWebhook(str, Enum):
    """Desenlace del webhook (para la respuesta y los tests; espejo de `AccionWa`)."""

    NO_REGISTRADO = "no_registrado"
    FIRMA_INVALIDA = "firma_invalida"
    SIN_ID = "sin_id"
    BODY_INVALIDO = "body_invalido"
    DUPLICADO = "duplicado"
    ACEPTADO = "aceptado"


@dataclass(frozen=True, slots=True)
class ResultadoWebhook:
    """Acción tomada + status HTTP a responder."""

    accion: AccionWebhook
    status: int


@dataclass(frozen=True, slots=True)
class WebhookResuelto:
    """Empresa + secret descifrado de un registro de webhook (lo devuelve el resolver del wiring)."""

    empresa_id: int
    secret: str | None


@dataclass(frozen=True, slots=True)
class WebhookMatiasDeps:
    """Puertos del webhook (los implementa el wiring; los tests los falsean).

    - `resolver(token)` → `WebhookResuelto | None` (control DB; None = token no registrado).
    - `registrar(empresa_id, webhook_id, evento, payload)` → `recibido_id | None` (tenant DB; None = duplicado).
    - `encolar(empresa_id, recibido_id)` → encola `procesar_webhook_matias` en el worker.
    """

    resolver: Callable[[str], Awaitable[WebhookResuelto | None]]
    registrar: Callable[[int, str, str, dict], Awaitable[int | None]]
    encolar: Callable[[int, int], Awaitable[None]]


def _evento_de(payload: dict) -> str:
    """Nombre del evento desde el cuerpo FIRMADO (`event`|`type`); '' si no viene (el worker lo ignora)."""
    valor = payload.get("event") or payload.get("type") or ""
    return str(valor)


async def manejar_webhook_matias(
    *, token: str, firma: str | None, webhook_id: str | None, cuerpo: bytes,
    deps: WebhookMatiasDeps, request_id: str | None = None,
) -> ResultadoWebhook:
    """Orquesta un webhook entrante. Toda la seguridad y la tenancy; sin FastAPI ni red (testeable)."""
    rid = request_id or uuid.uuid4().hex
    rid_token = request_id_var.set(rid)
    tid_token = tenant_id_var.set(None)
    try:
        # 1. Resolver empresa por el TOKEN del registro (jamás por el payload).
        resuelto = await deps.resolver(token)
        if resuelto is None:
            log.warning("matias_webhook_no_registrado")
            return ResultadoWebhook(AccionWebhook.NO_REGISTRADO, 404)
        tenant_id_var.set(resuelto.empresa_id)

        # 2. Firma sobre el cuerpo CRUDO, fail-closed. Antes de parsear o tocar nada.
        if not verificar_firma_matias(resuelto.secret, cuerpo, firma):
            log.warning("matias_webhook_firma_invalida")
            return ResultadoWebhook(AccionWebhook.FIRMA_INVALIDA, 401)

        # 3. Idempotencia exige el id de entrega.
        if not webhook_id:
            log.warning("matias_webhook_sin_id")
            return ResultadoWebhook(AccionWebhook.SIN_ID, 400)

        # 4. Parsear el cuerpo (objeto JSON).
        try:
            payload = json.loads(cuerpo)
        except (ValueError, UnicodeDecodeError):
            log.warning("matias_webhook_body_invalido")
            return ResultadoWebhook(AccionWebhook.BODY_INVALIDO, 400)
        if not isinstance(payload, dict):
            return ResultadoWebhook(AccionWebhook.BODY_INVALIDO, 400)

        # 5. Idempotencia: registrar el id en la base del tenant. Ya visto → duplicado (200).
        evento = _evento_de(payload)
        recibido_id = await deps.registrar(resuelto.empresa_id, webhook_id, evento, payload)
        if recibido_id is None:
            log.info("matias_webhook_duplicado", webhook_id=webhook_id)
            return ResultadoWebhook(AccionWebhook.DUPLICADO, 200)

        # 6. Delegar el procesamiento al worker (responde <5s).
        await deps.encolar(resuelto.empresa_id, recibido_id)
        log.info("matias_webhook_aceptado", webhook_id=webhook_id, evento=evento)
        return ResultadoWebhook(AccionWebhook.ACEPTADO, 200)
    finally:
        request_id_var.reset(rid_token)
        tenant_id_var.reset(tid_token)


def crear_router_matias() -> APIRouter:
    """Ruta `POST /webhooks/matias/{token}`. Lee el cuerpo CRUDO (firma) y toma las deps de `app.state`."""
    router = APIRouter(tags=["facturacion-webhook"])

    @router.post("/webhooks/matias/{token}")
    async def matias_webhook(token: str, request: Request) -> JSONResponse:
        cuerpo = await request.body()
        firma = request.headers.get("x-webhook-signature")
        webhook_id = request.headers.get("x-webhook-id")
        rid = request.headers.get("x-request-id")
        deps: WebhookMatiasDeps = request.app.state.matias_webhook_deps
        res = await manejar_webhook_matias(
            token=token, firma=firma, webhook_id=webhook_id, cuerpo=cuerpo, deps=deps, request_id=rid
        )
        return JSONResponse({"accion": res.accion.value}, status_code=res.status)

    return router
