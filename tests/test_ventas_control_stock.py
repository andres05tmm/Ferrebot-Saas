"""Control de stock por empresa (toggle `control_stock_estricto`) — camino crítico de ventas.

Dos planos: (1) HTTP por `POST /ventas` contra base efímera real, con la dependencia
`get_control_stock_estricto` overrideada (default PERMISIVO → 201 y stock negativo; ON → 409); (2) el
reader `cargar_control_stock_estricto` contra un control DB efímero (default False; lee 'true').
"""
import uuid

import httpx
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.auth import Principal, get_current_user
from core.config import get_settings
from core.db.session import get_tenant_db
from core.db.urls import tenant_url, to_async
from modules.ventas.config import cargar_control_stock_estricto
from modules.ventas.router import get_control_stock_estricto, router as ventas_router
from tests.conftest import create_database, drop_database


# ---- HTTP: POST /ventas con el toggle -------------------------------------
def _app(tenant, *, user_id: int, estricto: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(ventas_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol="vendedor")
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_control_stock_estricto] = lambda: estricto   # FAKE del flag de empresa
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_default_permisivo_vende_mas_que_stock_201_y_negativo(tenant, seed_producto):
    async with AsyncSession(tenant.engine) as s:
        uid, pid = await seed_producto(s, precio="10000", iva=19, stock="5")

    app = _app(tenant, user_id=uid, estricto=False)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json={"metodo_pago": "efectivo", "lineas": [{"producto_id": pid, "cantidad": 10}]})
    assert r.status_code == 201, r.text   # la venta SIEMPRE pasa en modo permisivo

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1
        tipo = (await s.execute(text("SELECT tipo FROM movimientos_inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert tipo == "SALIDA"
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock < 0   # 5 - 10 = -5: stock honesto en negativo


async def test_estricto_vende_mas_que_stock_409(tenant, seed_producto):
    async with AsyncSession(tenant.engine) as s:
        uid, pid = await seed_producto(s, precio="10000", iva=19, stock="5")

    app = _app(tenant, user_id=uid, estricto=True)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/ventas", json={"metodo_pago": "efectivo", "lineas": [{"producto_id": pid, "cantidad": 10}]})
    assert r.status_code == 409, r.text   # modo estricto: bloqueo por stock insuficiente

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 0   # nada registrado


# ---- Reader: cargar_control_stock_estricto (control DB) --------------------
async def test_cargar_control_stock_estricto_default_y_true(monkeypatch):
    name = f"test_control_stock_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    engine = create_async_engine(to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0})
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        async with AsyncSession(engine) as s:
            eid = (
                await s.execute(
                    text("INSERT INTO empresas (nombre, nit, slug, estado) VALUES ('PR','900','pr','activa') RETURNING id")
                )
            ).scalar_one()
            await s.commit()

            # Ausente → default PERMISIVO (False).
            assert await cargar_control_stock_estricto(s, eid) is False

            await s.execute(
                text("INSERT INTO config_empresa (empresa_id, clave, valor) VALUES (:e,'control_stock_estricto','true')"),
                {"e": eid},
            )
            await s.commit()
            # 'true' → estricto (True).
            assert await cargar_control_stock_estricto(s, eid) is True
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
