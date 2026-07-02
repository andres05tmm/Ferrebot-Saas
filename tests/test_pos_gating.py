"""Gating del retail en el API: features finas `ventas`/`caja`/`inventario` (ADR 0021, antes `pos`).

El retail va detrás de `require_feature` por feature FINA; `pos` (meta-pack) las satisface todas por
expansión (feature-flags.md: sin la capacidad, 404 'como si no existiera'). Patrón test_agenda_router:
app mínima + ASGITransport + dependency_overrides; la sesión sale del fixture `tenant` (DB efímera).

Invariante crítico (aislamiento por capacidad): estos tests van test-primero.
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.caja.router import gastos_router, router as caja_router
from modules.inventario.router import router as inventario_router, router_catalogo
from modules.ventas.router import router as ventas_router


def _app(tenant, caps: frozenset[str]) -> FastAPI:
    app = FastAPI()
    for r in (ventas_router, caja_router, gastos_router, inventario_router, router_catalogo):
        app.include_router(r, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol="vendedor")
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def test_router_pos_sin_feature_responde_404(tenant):
    # Empresa de servicios (con sus packs, SIN retail) → los routers retail no existen para ella.
    app = _app(tenant, frozenset({"pack_agenda", "pack_faq", "canal_whatsapp"}))
    async with _cliente(app) as c:
        for ruta in ("/api/v1/productos", "/api/v1/ventas", "/api/v1/gastos", "/api/v1/inventario/stock"):
            r = await c.get(ruta)
            assert r.status_code == 404, f"{ruta}: {r.text}"


async def test_router_pos_con_feature_responde_ok(tenant):
    # Con `pos` (meta-pack) activo —ferretería / Punto Rojo— TODO el retail responde normal (compat).
    app = _app(tenant, frozenset({"pos"}))
    async with _cliente(app) as c:
        for ruta in ("/api/v1/productos", "/api/v1/ventas", "/api/v1/gastos", "/api/v1/inventario/stock"):
            r = await c.get(ruta)
            assert r.status_code == 200, f"{ruta}: {r.text}"
            assert r.json() == []


async def test_solo_caja_ve_caja_y_nada_mas(tenant):
    # Peluquería con solo su contabilidad de caja: gastos sí; ventas/catálogo/stock no.
    app = _app(tenant, frozenset({"caja", "pack_agenda"}))
    async with _cliente(app) as c:
        assert (await c.get("/api/v1/gastos")).status_code == 200
        assert (await c.get("/api/v1/ventas")).status_code == 404
        assert (await c.get("/api/v1/productos")).status_code == 404
        assert (await c.get("/api/v1/inventario/stock")).status_code == 404


async def test_solo_ventas_ve_catalogo_sin_stock(tenant):
    # `ventas` trae el catálogo de productos (vende shampoo sin llevar stock); inventario/caja no.
    app = _app(tenant, frozenset({"ventas"}))
    async with _cliente(app) as c:
        assert (await c.get("/api/v1/productos")).status_code == 200
        assert (await c.get("/api/v1/ventas")).status_code == 200
        assert (await c.get("/api/v1/inventario/stock")).status_code == 404
        assert (await c.get("/api/v1/gastos")).status_code == 404


async def test_ventas_mas_inventario_habilita_stock(tenant):
    app = _app(tenant, frozenset({"ventas", "inventario"}))
    async with _cliente(app) as c:
        assert (await c.get("/api/v1/inventario/stock")).status_code == 200
        assert (await c.get("/api/v1/inventario/kardex/1")).status_code == 200
        assert (await c.get("/api/v1/gastos")).status_code == 404
