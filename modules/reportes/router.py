"""Router de reportes (B4, api-contract.md): GET /reportes/resumen (KPIs del día).

Núcleo (sin require_feature). Rol `vendedor` o superior; el vendedor efectivo lo decide el filtro
RBAC (`get_filtro_efectivo`). El repo se inyecta por dependencia (overridable en test) y el
`ReportesService` calcula los derivados.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_filtro_efectivo, require_role
from core.db.session import get_tenant_db
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.schemas import ResumenDia
from modules.reportes.service import ReportesService

router = APIRouter(tags=["reportes"])


def get_reportes_repo(session: AsyncSession = Depends(get_tenant_db)) -> SqlReportesRepository:
    """Repo de reportes sobre la sesión del tenant (overridable en test)."""
    return SqlReportesRepository(session)


@router.get("/reportes/resumen", response_model=ResumenDia)
async def resumen_dia(
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> ResumenDia:
    return await ReportesService(repo).resumen_dia(filtro)
