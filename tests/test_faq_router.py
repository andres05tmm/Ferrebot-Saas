"""Router del pack FAQ / conocimiento (dashboard) por HTTP contra base efímera real.

Patrón test_agenda_router: app mínima + ASGITransport + overrides de auth, sesión del tenant (commit) y
capacidades. Cubre: gating por flag (404 sin `pack_faq`), RBAC (admin escribe, staff lee) y el CRUD.
"""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.faq.router import router as faq_router

FLAG = frozenset({"pack_faq"})


def _app(tenant, *, rol: str = "admin", capacidades=FLAG) -> FastAPI:
    app = FastAPI()
    app.include_router(faq_router, prefix="/api/v1")

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
    app.dependency_overrides[get_capacidades] = lambda: capacidades
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def test_sin_flag_pack_faq_da_404(tenant):
    app = _app(tenant, capacidades=frozenset())  # sin la capacidad
    async with _cliente(app) as c:
        assert (await c.get("/api/v1/faq/conocimiento")).status_code == 404


async def test_rbac_staff_lee_admin_escribe(tenant):
    body = {"titulo": "Horarios", "contenido": "8 a 6"}
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        assert (await c.get("/api/v1/faq/conocimiento")).status_code == 200   # staff lee
        assert (await c.post("/api/v1/faq/conocimiento", json=body)).status_code == 403  # no escribe
    async with _cliente(_app(tenant, rol="admin")) as c:
        assert (await c.post("/api/v1/faq/conocimiento", json=body)).status_code == 201  # admin sí


async def test_crud_http(tenant):
    async with _cliente(_app(tenant, rol="admin")) as c:
        creada = await c.post("/api/v1/faq/conocimiento", json={"titulo": "Ubicación", "contenido": "Cra 1"})
        assert creada.status_code == 201
        cid = creada.json()["id"]

        lista = await c.get("/api/v1/faq/conocimiento")
        assert any(e["id"] == cid for e in lista.json())

        upd = await c.put(f"/api/v1/faq/conocimiento/{cid}", json={"titulo": "Ubicación", "contenido": "Cra 2"})
        assert upd.status_code == 200 and upd.json()["contenido"] == "Cra 2"
        assert upd.json()["actualizado_en"] is not None   # se serializa concreto

        assert (await c.delete(f"/api/v1/faq/conocimiento/{cid}")).status_code == 204
        assert (await c.get(f"/api/v1/faq/conocimiento/{cid}")).status_code == 404
