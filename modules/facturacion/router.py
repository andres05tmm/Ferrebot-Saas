"""Router de facturación electrónica: encola la emisión (no emite en el request).

Thin: valida feature + rol, crea el documento `pendiente` (reserva consecutivo, idempotente) y encola
`emitir_documento` en ARQ; la emisión real corre en el worker (`apps.worker`). Las dependencias glue
(`get_enqueuer`, `get_facturacion_service`) se overridean en tests y se cablean en el lifespan del API.

RED (E4e): las dependencias glue y el handler lanzan NotImplementedError; el shape es definitivo.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config import get_settings
from core.db.session import control_session, get_tenant_db
from modules.facturacion.config import cargar_config_matias
from modules.facturacion.repository import FacturaDetalle, FacturaLeer, SqlFacturacionRepository
from modules.facturacion.service import FacturacionService

router = APIRouter(tags=["facturacion"])


class FacturaCrear(BaseModel):
    """Cuerpo del POST /facturas: la venta a facturar."""

    venta_id: int


class Enqueuer(Protocol):
    """Puerto de cola: encola un job ARQ. En prod = pool ARQ del lifespan; en tests, un fake."""

    async def enqueue(self, job: str, *args) -> None: ...


class _ArqEnqueuer:
    """Adaptador sobre el pool ARQ del lifespan: `enqueue(job, *args)` → `enqueue_job`."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def enqueue(self, job: str, *args) -> None:
        await self._pool.enqueue_job(job, *args)


async def get_enqueuer(request: Request) -> Enqueuer:
    """Encolador sobre el pool ARQ creado en el lifespan del API (`app.state.arq_pool`)."""
    return _ArqEnqueuer(request.app.state.arq_pool)


def get_tenant_id(request: Request) -> int:
    """tenant_id de la empresa resuelta por el TenantMiddleware."""
    return request.state.tenant.id


def get_facturacion_repo(
    session: AsyncSession = Depends(get_tenant_db),
) -> SqlFacturacionRepository:
    """Repo de facturación sobre la sesión del tenant para las lecturas (overridable en test)."""
    return SqlFacturacionRepository(session)


async def get_facturacion_service(
    request: Request, session: AsyncSession = Depends(get_tenant_db)
) -> FacturacionService:
    """Arma el `FacturacionService` para `crear_pendiente` (sin credenciales MATIAS; eso es del worker).

    Solo necesita `ConfigFiscal.prefix`; la carga vía `cargar_config_matias` sobre una sesión de
    control per-call (ignora las credenciales). `matias=None`.
    """
    async with control_session() as cs:
        _cred, config = await cargar_config_matias(cs, get_settings().secrets_master_key, request.state.tenant.id)
    return FacturacionService(SqlFacturacionRepository(session), config=config)


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


@router.get(
    "/facturas", response_model=list[FacturaLeer],
    dependencies=[Depends(require_feature("facturacion_electronica"))],
)
async def listar_facturas(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    estado: str | None = Query(default=None),
    repo: SqlFacturacionRepository = Depends(get_facturacion_repo),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[FacturaLeer]:
    """Historial de facturas del rango (hora Colombia), filtrable por estado."""
    return await repo.listar(desde=desde, hasta=hasta, estado=estado)


@router.get(
    "/facturas/{factura_id}", response_model=FacturaDetalle,
    dependencies=[Depends(require_feature("facturacion_electronica"))],
)
async def detalle_factura(
    factura_id: int,
    repo: SqlFacturacionRepository = Depends(get_facturacion_repo),
    _user: Principal = Depends(require_role("vendedor")),
) -> FacturaDetalle:
    """Detalle de una factura (incluye el motivo de rechazo/error si aplica). 404 si no existe."""
    detalle = await repo.detalle(factura_id)
    if detalle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Factura {factura_id} no existe")
    return detalle
