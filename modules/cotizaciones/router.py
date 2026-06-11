"""Router del pack ventas/cotizaciones (dashboard). Gateado por `pack_ventas`.

Sin el flag, las rutas responden 404. RBAC: staff (vendedor+) lee y marca (aceptada/cancelada —
es quien cierra la venta); la config es de admin. Sin SQL aquí.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config.timezone import today_co
from core.db.session import get_tenant_db
from modules.cotizaciones.errors import CotizacionInexistente, EstadoInvalido
from modules.cotizaciones.repository import SqlCotizacionesRepository
from modules.cotizaciones.schemas import (
    CotizacionLeer,
    MarcarCotizacion,
    VentasWaConfigActualizar,
    VentasWaConfigLeer,
)
from modules.cotizaciones.service import CotizacionesService

router = APIRouter(
    prefix="/cotizaciones", tags=["cotizaciones"],
    dependencies=[Depends(require_feature("pack_ventas"))],
)


def get_cotizaciones_service(session: AsyncSession = Depends(get_tenant_db)) -> CotizacionesService:
    """Arma el `CotizacionesService` sobre la sesión del tenant (los tests lo overridean)."""
    return CotizacionesService(SqlCotizacionesRepository(session))


@router.get("", response_model=list[CotizacionLeer])
async def listar_cotizaciones(
    estado: list[str] | None = Query(default=None),
    service: CotizacionesService = Depends(get_cotizaciones_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> list[CotizacionLeer]:
    return await service.listar(estados=estado, hoy=today_co())


@router.put("/{cotizacion_id}/estado", response_model=CotizacionLeer)
async def marcar_cotizacion(
    cotizacion_id: int,
    payload: MarcarCotizacion,
    service: CotizacionesService = Depends(get_cotizaciones_service),
    _user: Principal = Depends(require_role("vendedor")),
) -> CotizacionLeer:
    try:
        return await service.marcar(cotizacion_id, payload.estado)
    except CotizacionInexistente as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cotización no encontrada") from exc
    except EstadoInvalido as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Estado inválido: {exc}") from exc


@router.get("/config", response_model=VentasWaConfigLeer)
async def obtener_config(
    service: CotizacionesService = Depends(get_cotizaciones_service),
    _user: Principal = Depends(require_role("admin")),
) -> VentasWaConfigLeer:
    return await service.obtener_config()


@router.put("/config", response_model=VentasWaConfigLeer)
async def actualizar_config(
    payload: VentasWaConfigActualizar,
    service: CotizacionesService = Depends(get_cotizaciones_service),
    _user: Principal = Depends(require_role("admin")),
) -> VentasWaConfigLeer:
    return await service.guardar_config(payload)
