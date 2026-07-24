"""Router de la cola de impresión (ADR 0033 D3). Gateado por `impresion` (404 sin él).

RBAC: toda la superficie es de **staff** (vendedor+): el agente local opera con la identidad del
dispositivo y el dashboard reimprime/pide precuentas. Sin SQL aquí.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.impresion.repository import SqlImpresionRepository
from modules.impresion.schemas import AckTrabajo, CrearTrabajo, TrabajoLeer
from modules.impresion.service import ImpresionService, OrigenInvalido, TrabajoInexistente

router = APIRouter(
    prefix="/impresion", tags=["impresion"],
    dependencies=[Depends(require_feature("impresion"))],
)


def get_impresion_service(session: AsyncSession = Depends(get_tenant_db)) -> ImpresionService:
    return ImpresionService(SqlImpresionRepository(session))


@router.get("/cola", response_model=list[TrabajoLeer])
async def cola(
    svc: ImpresionService = Depends(get_impresion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[TrabajoLeer]:
    """Entrega los trabajos pendientes (y los entregados vencidos) marcándolos `entregado_agente`."""
    return [TrabajoLeer.model_validate(t) for t in await svc.cola()]


@router.post("/trabajos", response_model=TrabajoLeer)
async def crear_trabajo(
    datos: CrearTrabajo,
    svc: ImpresionService = Depends(get_impresion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> TrabajoLeer:
    """Precuenta/comprobante bajo demanda. Idempotente: repetir el POST devuelve el mismo trabajo."""
    try:
        if datos.tipo == "precuenta":
            trabajo = await svc.crear_precuenta(datos.pedido_id)
        else:
            trabajo = await svc.crear_comprobante(datos.venta_id)
    except OrigenInvalido as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    return TrabajoLeer.model_validate(trabajo)


@router.post("/trabajos/{trabajo_id}/ack", response_model=TrabajoLeer)
async def ack(
    trabajo_id: int,
    datos: AckTrabajo,
    svc: ImpresionService = Depends(get_impresion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> TrabajoLeer:
    try:
        return TrabajoLeer.model_validate(
            await svc.ack(trabajo_id, ok=datos.ok, detalle=datos.detalle)
        )
    except TrabajoInexistente as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trabajo no existe") from e


@router.post("/trabajos/{trabajo_id}/reimprimir", response_model=TrabajoLeer)
async def reimprimir(
    trabajo_id: int,
    svc: ImpresionService = Depends(get_impresion_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> TrabajoLeer:
    try:
        return TrabajoLeer.model_validate(await svc.reimprimir(trabajo_id))
    except TrabajoInexistente as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trabajo no existe") from e
