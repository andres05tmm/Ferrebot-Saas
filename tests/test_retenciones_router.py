"""Router de retenciones (ADR 0027) por HTTP: gate de feature (404), admin-only (403) y CRUD/aplicar.

App mínima + ASGITransport + overrides de auth/sesión/capacidades (patrón test_compras).
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
from modules.retenciones.router import router


def _app(tenant, *, rol="admin", feature=True) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    caps = frozenset({"retenciones"}) if feature else frozenset()
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def test_sin_feature_404(tenant):
    app = _app(tenant, feature=False)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/retenciones/config")
    assert r.status_code == 404, r.text


async def test_config_es_admin_only_vendedor_403(tenant):
    app = _app(tenant, rol="vendedor")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/retenciones/config")
    assert r.status_code == 403, r.text


async def test_upsert_y_listar_config(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        put = await c.put("/api/v1/retenciones/config", json={"tipo": "retefuente", "concepto": "compras", "tarifa": "2.5"})
        lista = await c.get("/api/v1/retenciones/config")
    assert put.status_code == 200, put.text
    assert put.json()["tipo"] == "retefuente" and put.json()["editable"] is True
    assert len(lista.json()) == 1


async def test_upsert_tipo_invalido_422(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        r = await c.put("/api/v1/retenciones/config", json={"tipo": "inventado", "concepto": "x", "tarifa": "1"})
    assert r.status_code == 422, r.text


async def test_aplicar_venta_no_toca_total(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = (await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Ana','vendedor') RETURNING id"))).scalar_one()
        vid = (
            await s.execute(
                text(
                    "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
                    "metodo_pago, estado, origen) VALUES (1,:v,:f,1000000,190000,1190000,'efectivo','completada','web') RETURNING id"
                ),
                {"v": uid, "f": now_co()},
            )
        ).scalar_one()
        await s.commit()

    app = _app(tenant)
    async with _cliente(app) as c:
        await c.put("/api/v1/retenciones/config", json={"tipo": "retefuente", "concepto": "compras", "tarifa": "2.5"})
        r = await c.post(f"/api/v1/retenciones/venta/{vid}/aplicar")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_documento"] == "1190000.00"
    assert body["total_retenido"] == "25000.00"
    assert body["neto_a_recibir"] == "1165000.00"


async def test_aplicar_venta_inexistente_404(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/retenciones/venta/99999/aplicar")
    assert r.status_code == 404, r.text
