"""GET /api/v1/admin/tenants — panel super-admin (ADR 0010 §D2).

- Gate de rol (sin BD): un admin/vendedor de tenant → 403; sin token → 401.
- Listado (control DB efímero): un super_admin (JWT de plataforma) ve las empresas con slug, nombre,
  estado, plan, features EFECTIVAS y su número WhatsApp activo; ordenadas por slug.
"""
from __future__ import annotations

import uuid

import httpx
import psycopg
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport

import core.db.session as session_mod
from core.auth import create_access_token, create_platform_token
from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from modules.admin.router import router as admin_router
from tests.conftest import create_database, drop_database

_RUTA = "/api/v1/admin/tenants"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/v1")
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def _get(token: str | None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with _cliente(_app()) as c:
        return await c.get(_RUTA, headers=headers)


# --- gate de rol (sin BD) ----------------------------------------------------

async def test_admin_de_tenant_recibe_403():
    r = await _get(create_access_token(user_id=5, tenant="clinica", rol="admin"))
    assert r.status_code == 403


async def test_vendedor_recibe_403():
    r = await _get(create_access_token(user_id=9, tenant="clinica", rol="vendedor"))
    assert r.status_code == 403


async def test_sin_token_recibe_401():
    r = await _get(None)
    assert r.status_code == 401


async def test_super_admin_con_token_de_tenant_recibe_403():
    # Defensa en profundidad (ADR 0010 §D2): un token rol=super_admin pero scope=tenant (no de
    # plataforma) NO entra al panel; `require_platform` exige scope=platform.
    r = await _get(create_access_token(user_id=0, tenant="clinica", rol="super_admin"))
    assert r.status_code == 403


# --- listado real (control DB efímero) ---------------------------------------

def _control_efimero(monkeypatch) -> tuple[str, str]:
    name = f"test_control_admin_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)
    return name, url


def _seed(url: str) -> None:
    with psycopg.connect(to_libpq(url)) as conn:
        plan_id = conn.execute(
            "INSERT INTO planes (nombre, limites) VALUES ('Núcleo', '{\"features\": [\"pos\"]}') RETURNING id"
        ).fetchone()[0]
        pr = conn.execute(
            "INSERT INTO empresas (nombre, nit, slug, estado, plan_id) "
            "VALUES ('Punto Rojo','NIT-PR','puntorojo','activa',%s) RETURNING id",
            (plan_id,),
        ).fetchone()[0]
        # Override: añade 'fiados' a las features efectivas de Punto Rojo (sobre el plan {pos}).
        conn.execute(
            "INSERT INTO empresa_features (empresa_id, feature, habilitada) VALUES (%s,'fiados',true)", (pr,)
        )
        conn.execute(
            "INSERT INTO wa_numeros (phone_number_id, empresa_id, estado) VALUES ('PN-PR',%s,'activo')", (pr,)
        )
        # Segunda empresa SIN plan ni WhatsApp; ordena antes (slug 'aaa-demo').
        conn.execute(
            "INSERT INTO empresas (nombre, nit, slug, estado) VALUES ('Demo','NIT-D','aaa-demo','provisionando')"
        )
        conn.commit()


async def test_superadmin_lista_tenants(monkeypatch):
    name, url = _control_efimero(monkeypatch)
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        _seed(url)

        r = await _get(create_platform_token(user_id=0, rol="super_admin"))
        assert r.status_code == 200, r.text
        data = r.json()
        assert [t["slug"] for t in data] == ["aaa-demo", "puntorojo"]   # ordenado por slug

        demo, pr = data[0], data[1]
        assert demo == {
            "id": demo["id"], "slug": "aaa-demo", "nombre": "Demo", "estado": "provisionando",
            "plan": None, "features": [], "wa_numero": None,
        }
        assert pr["plan"] == "Núcleo"
        assert pr["features"] == ["fiados", "pos"]      # plan {pos} + override {fiados} efectivas, ordenadas
        assert pr["wa_numero"] == "PN-PR"
        assert pr["estado"] == "activa"
    finally:
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
