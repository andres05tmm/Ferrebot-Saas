"""Libro IVA (Fase 12, Slice 5) — router (servicio REAL, repo fake) + integración contra Postgres.

El router cruza el IVA generado (ventas) con el descontable (compras fiscales) de un rango. Está
gateado por la feature `libro_iva` (404 sin ella) y es admin-only (403 para vendedor). La parte de
router usa un repo fake (ejercita el ReportesService real + el gate de feature/rol); la agregación SQL
—que excluye anuladas y suma compras_fiscal— va en integración contra una base efímera real. SIN DIAN.
"""
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import now_co, rango_dia_co, today_co
from modules.reportes.repository import AgregadoLibroIVA, SqlReportesRepository
from modules.reportes.router import get_reportes_repo, router
from modules.reportes.service import ReportesService


# ---- Router (repo fake) ----------------------------------------------------
class _FakeLibroRepo:
    def __init__(self, agg: AgregadoLibroIVA) -> None:
        self._agg = agg

    async def libro_iva(self, *, inicio, fin) -> AgregadoLibroIVA:
        return self._agg


def _app(repo: _FakeLibroRepo, *, rol: str = "admin", feature: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_reportes_repo] = lambda: repo
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol=rol)
    caps = frozenset({"libro_iva"}) if feature else frozenset()
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


_AGG = AgregadoLibroIVA(
    base_ventas=Decimal("150000.00"), iva_generado=Decimal("28500.00"),
    base_compras=Decimal("100000.00"), iva_descontable=Decimal("19000.00"),
)


async def test_libro_iva_pinta_totales_y_saldo():
    app = _app(_FakeLibroRepo(_AGG), rol="admin")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/libro-iva")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["base_ventas"] == "150000.00"
    assert body["iva_generado"] == "28500.00"
    assert body["base_compras"] == "100000.00"
    assert body["iva_descontable"] == "19000.00"
    assert body["saldo"] == "9500.00"               # 28500 − 19000 (a pagar)
    assert body["desde"] and body["hasta"]          # rango por defecto (mes en curso) presente


async def test_libro_iva_saldo_a_favor_es_negativo():
    agg = AgregadoLibroIVA(
        base_ventas=Decimal("50000.00"), iva_generado=Decimal("9500.00"),
        base_compras=Decimal("100000.00"), iva_descontable=Decimal("19000.00"),
    )
    app = _app(_FakeLibroRepo(agg), rol="admin")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/libro-iva")
    assert r.status_code == 200, r.text
    assert r.json()["saldo"] == "-9500.00"          # descontable > generado → saldo a favor


async def test_libro_iva_sin_feature_404():
    app = _app(_FakeLibroRepo(_AGG), rol="admin", feature=False)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/libro-iva")
    assert r.status_code == 404, r.text             # como si la ruta no existiera


async def test_libro_iva_es_admin_only_vendedor_403():
    app = _app(_FakeLibroRepo(_AGG), rol="vendedor", feature=True)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/libro-iva")
    assert r.status_code == 403, r.text


# ---- Integración (Postgres efímero) ----------------------------------------
async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Ana','vendedor') RETURNING id"))
    ).scalar_one()


async def _venta(
    s: AsyncSession, *, consecutivo: int, vendedor_id: int, subtotal: str, impuestos: str,
    total: str, estado: str = "completada",
) -> None:
    await s.execute(
        text(
            "INSERT INTO ventas "
            "(consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago, estado, origen) "
            "VALUES (:c,:v,:f,:s,:i,:t,'efectivo',:e,'web')"
        ),
        {"c": consecutivo, "v": vendedor_id, "f": now_co(), "s": subtotal, "i": impuestos, "t": total, "e": estado},
    )


async def _compra_fiscal(s: AsyncSession, *, base: str, iva: str, total: str) -> None:
    await s.execute(
        text(
            "INSERT INTO compras_fiscal (proveedor_nit, base, iva, total, creado_en) "
            "VALUES ('900111', :b, :i, :t, :f)"
        ),
        {"b": base, "i": iva, "t": total, "f": now_co()},
    )


async def test_libro_iva_cuadra_y_excluye_anuladas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        a = await _usuario(s)
        # IVA generado/base de ventas = Σ de completadas; la anulada NO suma.
        await _venta(s, consecutivo=1, vendedor_id=a, subtotal="100000.00", impuestos="19000.00", total="119000.00")
        await _venta(s, consecutivo=2, vendedor_id=a, subtotal="50000.00", impuestos="9500.00", total="59500.00")
        await _venta(s, consecutivo=3, vendedor_id=a, subtotal="99999.00", impuestos="18999.00", total="118998.00", estado="anulada")
        # IVA descontable/base de compras = Σ de compras_fiscal.
        await _compra_fiscal(s, base="80000.00", iva="15200.00", total="95200.00")
        await _compra_fiscal(s, base="20000.00", iva="3800.00", total="23800.00")
        await s.commit()

    inicio, fin = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine) as s:
        agg = await SqlReportesRepository(s).libro_iva(inicio=inicio, fin=fin)
        out = await ReportesService(SqlReportesRepository(s)).libro_iva(desde=today_co(), hasta=today_co())

    assert agg.base_ventas == Decimal("150000.00")       # 100000 + 50000 (anulada fuera)
    assert agg.iva_generado == Decimal("28500.00")       # 19000 + 9500
    assert agg.base_compras == Decimal("100000.00")      # 80000 + 20000
    assert agg.iva_descontable == Decimal("19000.00")    # 15200 + 3800
    assert out.saldo == Decimal("9500.00")               # 28500 − 19000 (a pagar)


async def test_libro_iva_sin_datos_da_ceros(tenant):
    inicio, fin = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine) as s:
        agg = await SqlReportesRepository(s).libro_iva(inicio=inicio, fin=fin)
    assert agg.base_ventas == Decimal("0")
    assert agg.iva_generado == Decimal("0")
    assert agg.base_compras == Decimal("0")
    assert agg.iva_descontable == Decimal("0")
