"""Router del pack de conversación / handoff (dashboard) por HTTP contra base efímera real.

Patrón test_agenda_router: app mínima + ASGITransport + overrides de auth, sesión del tenant (que hace
commit, para persistir y entregar el pg_notify) y capacidades. Cubre: gating por flag (404), RBAC
(staff), listado de escaladas, la acción 'resolver' (estado→bot) y la emisión del evento SSE.
"""
import asyncio
import json

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from core.events.hub import event_hub
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.router import router as conversaciones_router

FLAG = frozenset({"canal_whatsapp"})
TEL_A = "573001112233"
TEL_B = "573009998877"


def _app(tenant, *, rol: str = "vendedor", capacidades=FLAG) -> FastAPI:
    app = FastAPI()
    app.include_router(conversaciones_router, prefix="/api/v1")

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


async def _escalar(tenant, telefono: str, motivo: str) -> int:
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        conv = await SqlConversacionRepository(s).escalar(telefono, motivo)
        await s.commit()
        return conv.id


# --- gating por flag --------------------------------------------------------
async def test_sin_flag_canal_whatsapp_da_404(tenant):
    app = _app(tenant, capacidades=frozenset())  # sin la capacidad
    async with _cliente(app) as c:
        r = await c.get("/api/v1/conversaciones/escaladas")
    assert r.status_code == 404


# --- listar escaladas -------------------------------------------------------
async def test_listar_escaladas(tenant):
    await _escalar(tenant, TEL_A, "no resuelvo")
    await _escalar(tenant, TEL_B, "queja")
    async with _cliente(_app(tenant)) as c:
        r = await c.get("/api/v1/conversaciones/escaladas")
    assert r.status_code == 200
    telefonos = {x["cliente_telefono"] for x in r.json()}
    assert telefonos == {TEL_A, TEL_B}
    assert all(x["estado"] == "humano" for x in r.json())


# --- resolver ---------------------------------------------------------------
async def test_resolver_devuelve_al_bot(tenant):
    cid = await _escalar(tenant, TEL_A, "x")
    async with _cliente(_app(tenant)) as c:
        r = await c.post(f"/api/v1/conversaciones/{cid}/resolver")
        assert r.status_code == 200
        assert r.json()["estado"] == "bot" and r.json()["resuelta_en"] is not None
        # Ya no aparece en la bandeja.
        assert (await c.get("/api/v1/conversaciones/escaladas")).json() == []


async def test_resolver_inexistente_da_404(tenant):
    async with _cliente(_app(tenant)) as c:
        r = await c.post("/api/v1/conversaciones/99999/resolver")
    assert r.status_code == 404


# --- SSE --------------------------------------------------------------------
async def test_resolver_emite_evento_sse(tenant):
    cid = await _escalar(tenant, TEL_A, "x")
    queue = await event_hub.subscribe(tenant_id=8484, dsn=tenant.url)
    try:
        async with _cliente(_app(tenant)) as c:
            r = await c.post(f"/api/v1/conversaciones/{cid}/resolver")
        assert r.status_code == 200
        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        evento = json.loads(payload)
        assert evento["event"] == "conversacion_resuelta"
        assert evento["data"]["conversacion_id"] == cid
    finally:
        await event_hub.unsubscribe(8484, queue)
