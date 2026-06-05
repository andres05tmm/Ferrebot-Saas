"""Compras fiscales (Fase 12, Slice 6a — DATOS, sin RADIAN) por HTTP contra base efímera real.

Patrón test_compras: app mínima + ASGITransport + overrides de auth, sesión del tenant (commit) y
capacidades (para el gate de la feature `compras_fiscal`). Cubre: registrar fiscal (desglose de IVA),
listado por rango, to-fiscal desde una compra normal (crea e idempotente), gate sin la feature → 404,
admin-only → 403 y validación de montos → 422. NO toca RADIAN/DIAN ni MATIAS.
"""
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.compras_fiscal.router import router as compras_fiscal_router


def _app(tenant, *, user_id: int = 1, rol: str = "admin", feature: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(compras_fiscal_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    caps = frozenset({"compras_fiscal"}) if feature else frozenset()
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def _seed_compra(s: AsyncSession, *, total: str = "50000") -> int:
    return (
        await s.execute(text("INSERT INTO compras (total) VALUES (:t) RETURNING id"), {"t": total})
    ).scalar_one()


# ---- Registrar -------------------------------------------------------------
async def test_registrar_compra_fiscal_persiste_desglose(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/compras-fiscal",
            json={"proveedor_nit": "900111", "base": 84033.61, "iva": 15966.39, "total": 100000},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["base"] == "84033.61" and body["iva"] == "15966.39" and body["total"] == "100000.00"
    assert body["proveedor_nit"] == "900111" and body["compra_id"] is None

    async with AsyncSession(tenant.engine) as s:
        base, iva, total = (
            await s.execute(text("SELECT base, iva, total FROM compras_fiscal WHERE proveedor_nit='900111'"))
        ).one()
        assert (base, iva, total) == (Decimal("84033.61"), Decimal("15966.39"), Decimal("100000.00"))


async def test_registrar_acepta_holgura_de_un_centavo(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        # base + iva = 100000.01 ≠ total exacto, pero dentro de la tolerancia de 1 centavo.
        r = await c.post(
            "/api/v1/compras-fiscal",
            json={"proveedor_nit": "901", "base": 84034.00, "iva": 15966.01, "total": 100000},
        )
    assert r.status_code == 201, r.text


# ---- Listado ---------------------------------------------------------------
async def test_listar_compras_fiscal_por_rango(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        await c.post("/api/v1/compras-fiscal", json={"proveedor_nit": "A", "base": 100, "iva": 19, "total": 119})
        await c.post("/api/v1/compras-fiscal", json={"proveedor_nit": "B", "base": 200, "iva": 38, "total": 238})
        actual = await c.get("/api/v1/compras-fiscal")                                    # default = mes en curso
        viejo = await c.get("/api/v1/compras-fiscal", params={"desde": "2020-01-01", "hasta": "2020-01-31"})
    assert actual.status_code == 200, actual.text
    assert len(actual.json()) == 2
    assert [x["proveedor_nit"] for x in actual.json()] == ["B", "A"]                      # más reciente primero
    assert viejo.json() == []


# ---- to-fiscal -------------------------------------------------------------
async def test_to_fiscal_crea_desde_compra_y_es_idempotente(tenant):
    async with AsyncSession(tenant.engine) as s:
        compra_id = await _seed_compra(s, total="50000")
        await s.commit()

    app = _app(tenant)
    async with _cliente(app) as c:
        r1 = await c.post(f"/api/v1/compras/{compra_id}/to-fiscal")
        r2 = await c.post(f"/api/v1/compras/{compra_id}/to-fiscal")
    assert r1.status_code == 201, r1.text          # la crea
    assert r2.status_code == 200, r2.text          # ya existía → idempotente
    assert r1.json()["total"] == "50000.00"        # toma el total de la compra
    assert r1.json()["base"] == "0.00" and r1.json()["iva"] == "0.00"   # desglose no conocido → 0
    assert r1.json()["compra_id"] == compra_id
    assert r2.json()["id"] == r1.json()["id"]      # devuelve la misma fiscal

    async with AsyncSession(tenant.engine) as s:
        n = (await s.execute(text("SELECT count(*) FROM compras_fiscal WHERE compra_id=:c"), {"c": compra_id})).scalar_one()
        assert n == 1                              # no duplica


async def test_to_fiscal_compra_inexistente_404(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/compras/999999/to-fiscal")
    assert r.status_code == 404, r.text


# ---- Gate de feature -------------------------------------------------------
async def test_sin_feature_404(tenant):
    app = _app(tenant, feature=False)
    async with _cliente(app) as c:
        post = await c.post("/api/v1/compras-fiscal", json={"proveedor_nit": "X", "base": 100, "iva": 19, "total": 119})
        lista = await c.get("/api/v1/compras-fiscal")
    assert post.status_code == 404, post.text      # como si la ruta no existiera
    assert lista.status_code == 404, lista.text


# ---- RBAC ------------------------------------------------------------------
async def test_compras_fiscal_es_solo_admin_vendedor_403(tenant):
    app = _app(tenant, rol="vendedor")             # con la feature, pero rol insuficiente
    async with _cliente(app) as c:
        post = await c.post("/api/v1/compras-fiscal", json={"proveedor_nit": "X", "base": 100, "iva": 19, "total": 119})
        lista = await c.get("/api/v1/compras-fiscal")
    assert post.status_code == 403, post.text
    assert lista.status_code == 403, lista.text


# ---- Validación ------------------------------------------------------------
async def test_montos_incoherentes_422(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/compras-fiscal", json={"proveedor_nit": "X", "base": 100, "iva": 19, "total": 200})
    assert r.status_code == 422, r.text            # base + iva ≠ total (fuera de tolerancia)


async def test_montos_negativos_422(tenant):
    app = _app(tenant)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/compras-fiscal", json={"proveedor_nit": "X", "base": -1, "iva": 0, "total": -1})
    assert r.status_code == 422, r.text            # Field(ge=0) lo rechaza
