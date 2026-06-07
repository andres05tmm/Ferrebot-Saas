"""Webhook único de WhatsApp (Kapso): `POST /wa/webhook`.

Flujo (orden NO negociable, security.md / tenancy.md §1):

  1. Validar la FIRMA HMAC sobre el cuerpo crudo (fail-closed) ANTES de procesar nada → 403 si falla.
  2. Solo el evento `whatsapp.message.received`; otros se ignoran (200).
  3. Parsear el cuerpo (JSON) y el mensaje (texto). No procesable → se ignora (200).
  4. Dedup por `message.id` (Kapso reintenta): un mensaje no se procesa dos veces (200).
  5. Resolver el tenant por `phone_number_id` (control DB → wa_numeros). No mapeado → log y descarta
     (200, sin abrir la base del tenant). Empresa inactiva → descarta (200).
  6. Construir el `Contexto` del pack (tenant + `cliente_telefono` SIEMPRE del payload, nunca de otra
     fuente) y delegar en `deps.procesar` (en este entregable: el eco; luego, el bucle del agente).

Responde 200 rápido y el trabajo pesado va encolado (`deps.procesar` no bloquea el webhook). Kapso
NO usa handshake GET: la verificación es por firma, por eso no hay ruta de challenge.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter
from starlette.requests import Request
from starlette.responses import JSONResponse

from ai.envelope import Contexto
from apps.wa.kapso import EVENTO_MENSAJE, parsear_mensaje, verificar_firma
from apps.wa.ports import AccionWa, ResultadoWa, WaDeps
from core.logging import get_logger, request_id_var, tenant_id_var

log = get_logger("wa.webhook")


async def manejar_mensaje(
    *,
    evento: str | None,
    firma: str | None,
    cuerpo: bytes,
    deps: WaDeps,
    request_id: str | None = None,
) -> ResultadoWa:
    """Orquesta un webhook entrante. Toda la seguridad y la tenancy; sin FastAPI ni red (testeable)."""
    rid = request_id or uuid.uuid4().hex
    rid_token = request_id_var.set(rid)
    tid_token = tenant_id_var.set(None)
    try:
        # 1. Firma sobre el cuerpo CRUDO, fail-closed. Antes de parsear o tocar nada.
        if not verificar_firma(deps.webhook_secret, cuerpo, firma):
            log.warning("wa_firma_invalida")
            return ResultadoWa(AccionWa.FIRMA_INVALIDA, 403)

        # 2. Solo el evento de mensaje entrante.
        if evento != EVENTO_MENSAJE:
            log.info("wa_evento_ignorado", evento=evento)
            return ResultadoWa(AccionWa.EVENTO_IGNORADO, 200)

        # 3. Parsear cuerpo + mensaje.
        try:
            payload = json.loads(cuerpo)
        except (ValueError, UnicodeDecodeError):
            log.warning("wa_body_invalido")
            return ResultadoWa(AccionWa.BODY_INVALIDO, 400)
        if not isinstance(payload, dict):
            return ResultadoWa(AccionWa.BODY_INVALIDO, 400)
        mensaje = parsear_mensaje(payload)
        if mensaje is None:
            log.info("wa_mensaje_ignorado")
            return ResultadoWa(AccionWa.MENSAJE_IGNORADO, 200)

        # 4. Dedup por id de mensaje (global: el wamid es único).
        if not await deps.dedup.marcar_si_nuevo(mensaje.message_id):
            log.info("wa_mensaje_duplicado", message_id=mensaje.message_id)
            return ResultadoWa(AccionWa.DUPLICADO, 200)

        # 5. Resolver el tenant por phone_number_id. No mapeado/inactivo → descartar (200).
        tenant = await deps.resolver.por_phone_number_id(mensaje.phone_number_id)
        if tenant is None:
            log.warning("wa_numero_no_mapeado", phone_number_id=mensaje.phone_number_id)
            return ResultadoWa(AccionWa.NO_MAPEADO, 200)
        if tenant.estado != "activa":
            tenant_id_var.set(tenant.id)
            log.info("wa_empresa_inactiva", estado=tenant.estado)
            return ResultadoWa(AccionWa.EMPRESA_INACTIVA, 200)
        tenant_id_var.set(tenant.id)

        # 6. Contexto del pack: cliente_telefono SIEMPRE del payload. Delegar (encola; no bloquea).
        ctx = Contexto(
            tenant_id=tenant.id,
            usuario_id=0,                       # canal público: no hay usuario de staff
            rol="cliente",
            origen="whatsapp",
            cliente_telefono=mensaje.telefono,
            request_id=rid,
        )
        await deps.procesar(mensaje, ctx)
        log.info("wa_mensaje_procesado", message_id=mensaje.message_id)
        return ResultadoWa(AccionWa.PROCESADO, 200, ctx)
    finally:
        request_id_var.reset(rid_token)
        tenant_id_var.reset(tid_token)


def crear_router_wa() -> APIRouter:
    """Ruta `POST /wa/webhook`. Lee el cuerpo CRUDO (para la firma) y toma las deps de `app.state`."""
    router = APIRouter(tags=["whatsapp"])

    @router.post("/wa/webhook")
    async def wa_webhook(request: Request) -> JSONResponse:
        cuerpo = await request.body()
        evento = request.headers.get("x-webhook-event")
        firma = request.headers.get("x-webhook-signature")
        rid = request.headers.get("x-request-id")
        deps: WaDeps = request.app.state.wa_deps
        res = await manejar_mensaje(
            evento=evento, firma=firma, cuerpo=cuerpo, deps=deps, request_id=rid
        )
        return JSONResponse({"accion": res.accion.value}, status_code=res.status)

    return router
