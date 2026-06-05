"""Guardarraíl de regresión: smokes HTTP de los routers núcleo (ventas, inventario, caja, fiados).

Blinda el wiring de dependencias —en particular que `get_tenant_db` NO se trate como query param
(el bug que destapó el smoke E2E de facturación)—. Cada caso afirma un status concreto: lo clave es
que NINGUNO devuelva 422 por query param faltante. Si un handler corre (200/201/404 de negocio), la
sesión del tenant se resolvió como dependencia y el glue está sano.

Sigue el patrón de tests/test_e2e_facturacion.py y tests/test_facturacion_router.py:
httpx.AsyncClient sobre ASGITransport(raise_app_exceptions=False), app FastAPI mínima por test,
overrides de auth (get_current_user) y de sesión (get_tenant_db) contra la base efímera del fixture.
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.db.session import get_tenant_db
from modules.caja.router import router as caja_router
from modules.fiados.router import router as fiados_router
from modules.inventario.router import router as inventario_router
from modules.ventas.router import router as ventas_router


def _app(router, tenant) -> FastAPI:
    """App mínima con el router montado y los overrides de auth + sesión del tenant efímero."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol="admin")
    app.dependency_overrides[get_tenant_db] = _db
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_inventario_listar_productos(tenant, seed_producto):
    async with AsyncSession(tenant.engine) as s:
        await seed_producto(s)
    app = _app(inventario_router, tenant)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/productos")
    assert r.status_code == 200, r.text
    assert len(r.json()) >= 1


async def test_caja_actual_sin_caja(tenant):
    app = _app(caja_router, tenant)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/caja/actual")
    # 404 de negocio (no 200, no 422): prueba que get_tenant_db resolvió y el handler corrió.
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "No hay caja abierta"


async def test_fiados_listar_deudas(tenant):
    app = _app(fiados_router, tenant)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/fiados/deudas")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


async def test_ventas_crear(tenant, seed_producto):
    async with AsyncSession(tenant.engine) as s:
        _usuario_id, producto_id = await seed_producto(s)
    app = _app(ventas_router, tenant)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/ventas",
            json={"metodo_pago": "efectivo", "lineas": [{"producto_id": producto_id, "cantidad": 2}]},
        )
    assert r.status_code == 201, r.text
