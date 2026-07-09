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
from modules.reportes.libros import LibrosService, SqlLibrosRepository
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.schemas import (
    AgingProveedor,
    CuentaMayor,
    DiaCalendarioLeer,
    EstadoResultados,
    FlujoDinero,
    LibroIVA,
    MargenProducto,
    MovimientoAuxiliar,
    ProyeccionCaja,
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


def get_libros_repo(session: AsyncSession = Depends(get_tenant_db)) -> SqlLibrosRepository:
    """Repo de libros contables sobre la sesión del tenant (overridable en test)."""
    return SqlLibrosRepository(session)


@router.get(
    "/reportes/libro-mayor",
    response_model=list[CuentaMayor],
    dependencies=[Depends(require_feature("libros_contables"))],
)
async def libro_mayor(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    repo: SqlLibrosRepository = Depends(get_libros_repo),
    _user: Principal = Depends(require_role("admin")),
) -> list[CuentaMayor]:
    """Libro Mayor del rango (default mes): total por cuenta/concepto. Admin-only, feature `libros_contables`."""
    return await LibrosService(repo).mayor(desde=desde, hasta=hasta)


@router.get(
    "/reportes/libro-auxiliar",
    response_model=list[MovimientoAuxiliar],
    dependencies=[Depends(require_feature("libros_contables"))],
)
async def libro_auxiliar(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    concepto: str | None = Query(default=None),
    repo: SqlLibrosRepository = Depends(get_libros_repo),
    _user: Principal = Depends(require_role("admin")),
) -> list[MovimientoAuxiliar]:
    """Libro Auxiliar del rango (default mes): detalle documento a documento, filtrable por `concepto`."""
    return await LibrosService(repo).auxiliar(desde=desde, hasta=hasta, concepto=concepto)


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
    "/reportes/flujo-dinero",
    response_model=FlujoDinero,
    # Cashflow del negocio completo: dinero real que entró y salió. Feature `caja` (la superficie
    # de dinero físico); admin-only como resultados.
    dependencies=[Depends(require_feature("caja"))],
)
async def flujo_dinero(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("admin")),
) -> FlujoDinero:
    """Flujo de dinero simple del rango (default mes): entradas (ventas cobradas + abonos de fiados
    + ingresos de caja) vs salidas (gastos + abonos a proveedor + egresos de caja) y el neto. No
    exige `contabilidad_ledger`."""
    return await ReportesService(repo).flujo_dinero(desde=desde, hasta=hasta)


@router.get(
    "/reportes/margen-productos",
    response_model=list[MargenProducto],
    dependencies=[Depends(require_feature("ventas"))],
)
async def margen_productos(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    por: str = Query(default="producto", pattern="^(producto|categoria)$"),
    limite: int = Query(default=50, ge=1, le=200),
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("admin")),
) -> list[MargenProducto]:
    """Margen bruto por producto o categoría (default mes): ingresos sin IVA vs COGS snapshot, con
    `cobertura_pct` honesta (unidades con costo registrado). Excluye ventas varia. Admin-only."""
    return await ReportesService(repo).margen_productos(
        desde=desde, hasta=hasta, por=por, limite=limite
    )


@router.get(
    "/reportes/aging-cxp",
    response_model=list[AgingProveedor],
    # La cartera por pagar vive sobre la superficie de compras/proveedores → `inventario`.
    dependencies=[Depends(require_feature("inventario"))],
)
async def aging_cxp(
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("admin")),
) -> list[AgingProveedor]:
    """Cartera por pagar por proveedor en tramos de antigüedad (0-30/31-60/61-90/90+) con semáforo."""
    return await ReportesService(repo).aging_cxp()


@router.get("/reportes/proyeccion-caja", response_model=ProyeccionCaja)
async def proyeccion_caja(
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("admin")),
) -> ProyeccionCaja:
    """Proyección del cierre del mes con el promedio de los últimos 14 días con movimiento (fórmula
    del dashboard viejo). Núcleo (degrada a ceros sin datos); admin-only."""
    return await ReportesService(repo).proyeccion_caja()


@router.get(
    "/reportes/calendario",
    response_model=list[DiaCalendarioLeer],
    dependencies=[Depends(require_feature("ventas"))],
)
async def calendario_mensual(
    anio: int = Query(ge=2000, le=2100),
    mes: int = Query(ge=1, le=12),
    repo: SqlReportesRepository = Depends(get_reportes_repo),
    _user: Principal = Depends(require_role("vendedor")),
    filtro: int | None = Depends(get_filtro_efectivo),
) -> list[DiaCalendarioLeer]:
    """Agregado diario del mes (heatmap del historial): total vendido, transacciones y gastos por
    día Colombia; el vendedor efectivo lo da el filtro RBAC (los gastos no se scopean)."""
    return await ReportesService(repo).calendario(anio=anio, mes=mes, vendedor_id=filtro)


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
