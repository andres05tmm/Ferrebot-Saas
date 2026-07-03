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
from modules.reportes.consolidacion import (
    ConsolidacionIVAService,
    SqlConsolidacionRepository,
)
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.schemas import (
    EstadoResultados,
    LibroIVA,
    PuntoSerie,
    ResumenDia,
    SaldoBimestral,
    TopProducto,
    TotalesVentas,
)
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


@router.get("/reportes/serie-ventas", response_model=list[PuntoSerie])
async def serie_ventas(
    dias: int = Query(default=30, ge=1, le=365),
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> list[PuntoSerie]:
    """Serie diaria de ventas de los últimos `dias` (default 30, hora Colombia), para la gráfica de
    evolución y el sparkline; el vendedor efectivo lo da el filtro RBAC."""
    return await ReportesService(repo).serie_ventas(dias=dias, vendedor_id=filtro)


@router.get("/reportes/totales", response_model=TotalesVentas)
async def totales_ventas(
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> TotalesVentas:
    """Totales de ventas: hoy / últimos 7 días / mes en curso (hora Colombia), acotados al vendedor."""
    return await ReportesService(repo).totales(vendedor_id=filtro)


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


def get_consolidacion_repo(
    session: AsyncSession = Depends(get_tenant_db),
) -> SqlConsolidacionRepository:
    """Repo de consolidación de IVA sobre la sesión del tenant (overridable en test)."""
    return SqlConsolidacionRepository(session)


@router.post(
    "/reportes/iva/consolidar",
    response_model=SaldoBimestral,
    dependencies=[Depends(require_feature("libro_iva"))],
)
async def consolidar_iva(
    anio: int = Query(ge=2000, le=2100),
    bimestre: int = Query(ge=1, le=6),
    repo: SqlConsolidacionRepository = Depends(get_consolidacion_repo),
    _user: Principal = Depends(require_role("admin")),
) -> SaldoBimestral:
    """Materializa el Libro IVA + el saldo del bimestre (ADR 0027). Idempotente: reprocesar no duplica.
    Admin-only; gateado por la feature `libro_iva`. Solo cruza datos existentes; no toca la DIAN."""
    return await ConsolidacionIVAService(repo).consolidar_bimestre(anio=anio, bimestre=bimestre)


@router.get(
    "/reportes/iva-saldos",
    response_model=list[SaldoBimestral],
    dependencies=[Depends(require_feature("libro_iva"))],
)
async def listar_iva_saldos(
    anio: int | None = Query(default=None),
    repo: SqlConsolidacionRepository = Depends(get_consolidacion_repo),
    _user: Principal = Depends(require_role("admin")),
) -> list[SaldoBimestral]:
    """Saldos bimestrales de IVA ya consolidados (todos, o los del año dado). Admin-only, feature `libro_iva`."""
    return await ConsolidacionIVAService(repo).listar_saldos(anio=anio)


@router.get(
    "/reportes/top-productos",
    response_model=list[TopProducto],
    # Ranking de productos: gateado por la feature fina `ventas` (ADR 0021). El resto de reportes
    # —resumen ("Hoy"), serie/totales, resultados financieros— es núcleo y degrada a ceros sin ventas.
    dependencies=[Depends(require_feature("ventas"))],
)
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
