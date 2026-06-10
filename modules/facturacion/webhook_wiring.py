"""Composition root del webhook MATIAS: arma `WebhookMatiasDeps` con los adaptadores reales.

Smoke manual (como `apps.wa.wiring`): la lógica vive en `modules.facturacion.webhook` (testeable con
fakes). Aquí solo el cableado: resolver token→empresa+secret en el control DB, registrar el `webhook_id`
en la base del tenant (idempotencia) y encolar `procesar_webhook_matias` en ARQ."""
from __future__ import annotations

from typing import Any

from core.config import get_settings
from core.db.session import control_session, tenant_session
from core.logging import get_logger
from core.tenancy.control_repo import resolve_tenant_by_id
from modules.facturacion.repository import SqlFacturacionRepository
from modules.facturacion.webhook import WebhookMatiasDeps, WebhookResuelto
from modules.facturacion.webhook_repo import buscar_empresa_por_token, leer_secret_webhook

log = get_logger("facturacion.webhook.wiring")

# Job ARQ del procesamiento (registrado en apps.worker.main): el webhook encola, el worker aplica.
JOB_WEBHOOK = "procesar_webhook_matias"


def construir_webhook_matias_deps(arq_pool: Any) -> WebhookMatiasDeps:
    """Arma `WebhookMatiasDeps` con los adaptadores reales (lo llama el lifespan del API)."""
    master = get_settings().secrets_master_key

    async def resolver(token: str) -> WebhookResuelto | None:
        async with control_session() as cs:
            empresa_id = await buscar_empresa_por_token(cs, token)
            if empresa_id is None:
                return None
            secret = await leer_secret_webhook(cs, master, empresa_id)
        return WebhookResuelto(empresa_id=int(empresa_id), secret=secret)

    async def registrar(empresa_id: int, webhook_id: str, evento: str, payload: dict) -> int | None:
        async with control_session() as cs:
            tenant = await resolve_tenant_by_id(cs, empresa_id)
        if tenant is None:
            log.warning("matias_webhook_tenant_inexistente", empresa_id=empresa_id)
            return None
        recibido_id: int | None = None
        async for s in tenant_session(tenant):   # commit al cerrar el generador
            recibido_id = await SqlFacturacionRepository(s).registrar_recibido(webhook_id, evento, payload)
        return recibido_id

    async def encolar(empresa_id: int, recibido_id: int) -> None:
        await arq_pool.enqueue_job(JOB_WEBHOOK, empresa_id, recibido_id)

    return WebhookMatiasDeps(resolver=resolver, registrar=registrar, encolar=encolar)
