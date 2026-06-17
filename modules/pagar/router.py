"""Router del pack pagar (backend de la página Cuentas por pagar del dashboard). Gateado por `pack_pagar`.

Sin el flag, las rutas responden 404 (como si no existieran). RBAC: TODO el router es de **admin** —
las cuentas por pagar son información sensible del negocio (saldos con proveedores). El aviso es
INTERNO al dueño (ADR 0019): por eso, a diferencia de cobranza, no hay opt-out ni promesas. La lógica
vive en `PagarService`; aquí solo se valida, se mapea a HTTP y se serializa.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import require_feature
from core.config.timezone import today_co
from core.db.session import get_tenant_db
from modules.pagar.repository import SqlPagarRepository
from modules.pagar.schemas import (
    CuentaPorPagarLeer,
    PagarConfigActualizar,
    PagarConfigLeer,
)
from modules.pagar.service import PagarService

# Todo el router exige el flag pack_pagar (sin él, 404 — como si no existiera).
router = APIRouter(
    prefix="/pagar", tags=["pagar"],
    dependencies=[Depends(require_feature("pack_pagar"))],
)


def get_pagar_service(session: AsyncSession = Depends(get_tenant_db)) -> PagarService:
    """Arma el `PagarService` sobre la sesión del tenant (los tests lo overridean)."""
    return PagarService(SqlPagarRepository(session))


@router.get("/cuentas", response_model=list[CuentaPorPagarLeer])
async def listar_cuentas(
    service: PagarService = Depends(get_pagar_service),
    _user: Principal = Depends(require_role("admin")),
) -> list[CuentaPorPagarLeer]:
    """Cuentas por pagar con saldo, clasificadas (por vencer / vencidas) con su vencimiento efectivo."""
    return await service.cuentas_por_pagar(today_co())


@router.get("/config", response_model=PagarConfigLeer)
async def obtener_config(
    service: PagarService = Depends(get_pagar_service),
    _user: Principal = Depends(require_role("admin")),
) -> PagarConfigLeer:
    return await service.obtener_config()


@router.put("/config", response_model=PagarConfigLeer)
async def actualizar_config(
    payload: PagarConfigActualizar,
    service: PagarService = Depends(get_pagar_service),
    _user: Principal = Depends(require_role("admin")),
) -> PagarConfigLeer:
    return await service.guardar_config(payload)
