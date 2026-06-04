"""Router de facturación electrónica: encola la emisión (no emite en el request).

Thin: valida feature + rol, crea el documento `pendiente` (reserva consecutivo, idempotente) y encola
`emitir_documento` en ARQ; la emisión real corre en el worker (`apps.worker`). Las dependencias glue
(`get_enqueuer`, `get_facturacion_service`) se overridean en tests y se cablean en el lifespan del API.

RED (E4e): las dependencias glue y el handler lanzan NotImplementedError; el shape es definitivo.
"""
from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, Depends, Header, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.facturacion.repository import FacturaLeer
from modules.facturacion.service import FacturacionService

router = APIRouter(tags=["facturacion"])


class FacturaCrear(BaseModel):
    """Cuerpo del POST /facturas: la venta a facturar."""

    venta_id: int


class Enqueuer(Protocol):
    """Puerto de cola: encola un job ARQ. En prod = pool ARQ del lifespan; en tests, un fake."""

    async def enqueue(self, job: str, *args) -> None: ...


async def get_enqueuer() -> Enqueuer:
    """Pool ARQ para encolar (glue; override en tests; en prod lo arma el lifespan del API)."""
    raise NotImplementedError("E4e GREEN: get_enqueuer (pool ARQ)")


def get_tenant_id(request: Request) -> int:
    """tenant_id de la empresa resuelta por el TenantMiddleware."""
    return request.state.tenant.id


async def get_facturacion_service(
    request: Request, session: AsyncSession = Depends(get_tenant_db)
) -> FacturacionService:
    """Arma el `FacturacionService` de la empresa (glue; override en tests).

    GREEN: `SqlFacturacionRepository(session)` + `MatiasClient` + `ConfigFiscal` descifrada del
    control DB (`cargar_config_matias`).
    """
    raise NotImplementedError("E4e GREEN: get_facturacion_service (repo tenant + config control)")


@router.post(
    "/facturas", response_model=FacturaLeer, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_feature("facturacion_electronica"))],
)
async def crear_factura(
    payload: FacturaCrear,
    user: Principal = Depends(require_role("vendedor")),
    service: FacturacionService = Depends(get_facturacion_service),
    enqueuer: Enqueuer = Depends(get_enqueuer),
    tenant_id: int = Depends(get_tenant_id),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> FacturaLeer:
    """Crea el pendiente (idempotente) y encola `emitir_documento(tenant_id, factura_id)`."""
    f = await service.crear_pendiente(payload.venta_id, idempotency_key)
    await enqueuer.enqueue("emitir_documento", tenant_id, f.id)
    return f
