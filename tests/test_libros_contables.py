"""Libros auxiliar y mayor (ADR 0027) contra Postgres efímero + gate por HTTP.

El Mayor totaliza cada concepto del período; el Auxiliar lista el detalle documento a documento. Cubre:
totales por concepto (ingresos, IVA generado, gastos, compras, IVA descontable, retenciones), detalle
filtrable por concepto, gate de feature `libros_contables` (404) y admin-only (403), y aislamiento.
"""
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import now_co
from core.db.session import get_tenant_db
from modules.reportes.libros import LibrosService, SqlLibrosRepository
from modules.reportes.router import router


async def _sembrar(s: AsyncSession) -> None:
    uid = (await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('A','vendedor') RETURNING id"))).scalar_one()
    await s.execute(
        text(
            "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago, estado, origen) "
            "VALUES (1,:v,:f,100000,19000,119000,'efectivo','completada','web')"
        ),
        {"v": uid, "f": now_co()},
    )
    await s.execute(
        text("INSERT INTO gastos (categoria, concepto, monto, creado_en) VALUES ('servicios', 'Arriendo', 30000, :f)"),
        {"f": now_co()},
    )
    await s.execute(
        text("INSERT INTO compras_fiscal (proveedor_nit, base, iva, total, creado_en) VALUES ('900',40000,7600,47600,:f)"),
        {"f": now_co()},
    )
    await s.execute(
        text(
            "INSERT INTO retenciones_documento (doc_tipo, doc_id, tipo, concepto, base, tarifa, valor, creado_en) "
            "VALUES ('venta',1,'retefuente','compras',100000,2.5,2500,:f)"
        ),
        {"f": now_co()},
    )
    await s.commit()


async def test_mayor_totaliza_conceptos(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar(s)
        mayor = await LibrosService(SqlLibrosRepository(s)).mayor(desde=None, hasta=None)
    por_concepto = {c.concepto: c.total for c in mayor}
    assert por_concepto["ingresos_ventas"] == Decimal("100000.00")
    assert por_concepto["iva_generado"] == Decimal("19000.00")
    assert por_concepto["gastos"] == Decimal("30000.00")
    assert por_concepto["compras"] == Decimal("40000.00")
    assert por_concepto["iva_descontable"] == Decimal("7600.00")
    assert por_concepto["retefuente"] == Decimal("2500.00")
    # Conceptos en cero no aparecen (no hubo costo de ventas).
    assert "costo_ventas" not in por_concepto


async def test_auxiliar_filtra_por_concepto(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar(s)
        svc = LibrosService(SqlLibrosRepository(s))
        todos = await svc.auxiliar(desde=None, hasta=None, concepto=None)
        solo_ventas = await svc.auxiliar(desde=None, hasta=None, concepto="ingresos_ventas")
    assert len(todos) == 4                       # venta + gasto + compra + retencion
    assert len(solo_ventas) == 1
    assert solo_ventas[0].referencia == "venta:1"
    assert solo_ventas[0].valor == Decimal("100000.00")


# ── HTTP: gate + RBAC ─────────────────────────────────────────────────────────
def _app(tenant, *, rol="admin", feature=True) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    caps = frozenset({"libros_contables"}) if feature else frozenset()
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def test_libro_mayor_sin_feature_404(tenant):
    app = _app(tenant, feature=False)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/libro-mayor")
    assert r.status_code == 404, r.text


async def test_libro_mayor_vendedor_403(tenant):
    app = _app(tenant, rol="vendedor")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/libro-mayor")
    assert r.status_code == 403, r.text


async def test_libro_mayor_admin_ok(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _sembrar(s)
    app = _app(tenant)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/libro-mayor")
    assert r.status_code == 200, r.text
    conceptos = {row["concepto"] for row in r.json()}
    assert "ingresos_ventas" in conceptos
