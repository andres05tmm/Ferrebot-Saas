"""Router de reportes (B4, api-contract.md): GET /reportes/resumen (KPIs del día).

Núcleo (sin require_feature). Rol `vendedor` o superior; el vendedor efectivo lo decide el filtro
RBAC (`get_filtro_efectivo`). El repo se inyecta por dependencia (overridable en test) y el
`ReportesService` calcula los derivados.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_filtro_efectivo, require_role
from core.auth.features import require_feature
from core.db.session import get_tenant_db
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.schemas import EstadoResultados, LibroIVA, ResumenDia, TopProducto
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


@router.get("/reportes/resultados", response_model=EstadoResultados)
async def estado_resultados(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("admin")),
) -> EstadoResultados:
    """Estado de resultados del rango (default mes). Admin-only: es del negocio completo, sin scoping."""
    return await ReportesService(repo).estado_resultados(desde=desde, hasta=hasta)


@router.get(
    "/reportes/libro-iva",
    response_model=LibroIVA,
    dependencies=[Depends(require_feature("libro_iva"))],
)
async def libro_iva(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("admin")),
) -> LibroIVA:
    """Libro IVA del rango (default mes). Admin-only; gateado por la feature `libro_iva`. Solo cruza
    datos existentes (ventas vs compras fiscales): NO emite ni consulta a la DIAN."""
    return await ReportesService(repo).libro_iva(desde=desde, hasta=hasta)


@router.get("/reportes/top-productos", response_model=list[TopProducto])
async def top_productos(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    limite: int = Query(default=10, ge=1, le=100),
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> list[TopProducto]:
    """Ranking de productos por ingreso del rango (default mes); el vendedor efectivo lo da el filtro RBAC."""
    return await ReportesService(repo).top_productos(
        desde=desde, hasta=hasta, vendedor_id=filtro, limite=limite
    )
