"""Gating del pack `pos` (ADR 0008) en el API: los routers POS responden 404 sin la capacidad `pos`.

El POS dejó de ser núcleo; ahora va detrás de `require_feature("pos")`, igual que los demás packs
(feature-flags.md: sin la capacidad, 404 'como si no existiera'). Patrón test_agenda_router: app
mínima + ASGITransport + dependency_overrides; la sesión sale del fixture `tenant` (DB efímera).
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.inventario.router import router as inventario_router


def _app(tenant, caps: frozenset[str]) -> FastAPI:
    app = FastAPI()
    app.include_router(inventario_router, prefix="/api/v1")

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
    # Empresa de servicios (con sus packs, SIN pos) → el router POS no existe para ella.
    app = _app(tenant, frozenset({"pack_agenda", "pack_faq", "canal_whatsapp"}))
    async with _cliente(app) as c:
        r = await c.get("/api/v1/productos")
    assert r.status_code == 404, r.text


async def test_router_pos_con_feature_responde_ok(tenant):
    # Con `pos` activo (ferretería / Punto Rojo) el router responde normal (lista vacía, 200).
    app = _app(tenant, frozenset({"pos"}))
    async with _cliente(app) as c:
        r = await c.get("/api/v1/productos")
    assert r.status_code == 200, r.text
    assert r.json() == []
