"""Endpoints del panel super-admin (modules.admin.router, ADR 0010 §B3). Bajo /api/v1/admin.

- POST /admin/tenants: valida server-side y ENCOLA (enqueuer fake) → {job_id}; inválido/slug malo → 4xx
  sin encolar.
- GET /admin/jobs/{id}: refleja el estado (store fake); 404 si no existe.
- PUT /admin/tenants/{slug}/features: respeta el catálogo (feature desconocida → 4xx); toggle válido
  (control DB efímero) devuelve el set efectivo.
- POST /admin/tenants/{slug}/identidad-admin: crea la identidad admin + token (tenant provisionado).
- Un admin/vendedor (NO plataforma) → 403 en todas.
"""
from __future__ import annotations

import uuid

import httpx
import psycopg
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport
from psycopg.rows import dict_row

from core.auth import create_access_token, create_platform_token
from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from modules.admin.router import get_enqueuer, get_estado_provision, router as admin_router
from tests.conftest import create_database, drop_database
from tools.provision_tenant import _db_name, provision_tenant_full

_MANIFIESTO_OK = {
    "version": 1,
    "identidad": {"slug": "clinica-x", "nombre": "Clínica X", "nit": "NIT-X"},
    "admin": {"nombre": "Admin"},
}


class FakeEnqueuer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def enqueue(self, job: str, *args) -> None:
        self.calls.append((job, args))


class FakeEstado:
    def __init__(self) -> None:
        self.store: dict[str, dict] = {}

    async def marcar(self, job_id: str, estado: str, **campos) -> dict:
        d = self.store.get(job_id, {"job_id": job_id})
        d.update(estado=estado, **campos)
        self.store[job_id] = d
        return d

    async def obtener(self, job_id: str) -> dict | None:
        return self.store.get(job_id)


def _app(enq: FakeEnqueuer | None = None, est: FakeEstado | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router, prefix="/api/v1")
    if enq is not None:
        app.dependency_overrides[get_enqueuer] = lambda: enq
    if est is not None:
        app.dependency_overrides[get_estado_provision] = lambda: est
    return app


def _cli(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


def _plataforma() -> dict:
    return {"Authorization": f"Bearer {create_platform_token(user_id=0, rol='super_admin')}"}


def _control_efimero(monkeypatch) -> tuple[str, str]:
    name = f"test_control_b3_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    return name, url


# ── POST /admin/tenants (encolar) ────────────────────────────────────────────

async def test_post_tenants_valida_encola_y_devuelve_job_id():
    enq, est = FakeEnqueuer(), FakeEstado()
    async with _cli(_app(enq, est)) as c:
        r = await c.post("/api/v1/admin/tenants", json=_MANIFIESTO_OK, headers=_plataforma())
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    # Encoló provisionar_tenant(manifiesto_dict, job_id).
    assert len(enq.calls) == 1
    job, args = enq.calls[0]
    assert job == "provisionar_tenant"
    assert args[0]["identidad"]["slug"] == "clinica-x" and args[1] == job_id
    # Estado marcado 'encolado' con el slug.
    assert est.store[job_id]["estado"] == "encolado" and est.store[job_id]["slug"] == "clinica-x"


async def test_post_tenants_slug_invalido_422_sin_encolar():
    enq, est = FakeEnqueuer(), FakeEstado()
    m = {**_MANIFIESTO_OK, "identidad": {"slug": "Bad_Slug!", "nombre": "X", "nit": "N"}}
    async with _cli(_app(enq, est)) as c:
        r = await c.post("/api/v1/admin/tenants", json=m, headers=_plataforma())
    assert r.status_code == 422
    assert enq.calls == []          # nunca se encoló


async def test_post_tenants_incoherente_422_sin_encolar():
    # Forma+slug válidos, pero datos de agenda sin pack_agenda activo → validar() falla (semántica).
    enq, est = FakeEnqueuer(), FakeEstado()
    m = {
        "version": 1,
        "identidad": {"slug": "clinica-y", "nombre": "Y", "nit": "N"},
        "plan": {"nombre": "P", "features": ["pack_faq"]},
        "packs": {"agenda": {"servicios": [{"nombre": "S", "duracion_min": 10}]}},
    }
    async with _cli(_app(enq, est)) as c:
        r = await c.post("/api/v1/admin/tenants", json=m, headers=_plataforma())
    assert r.status_code == 422
    assert enq.calls == []


# ── GET /admin/jobs/{job_id} ─────────────────────────────────────────────────

async def test_get_job_refleja_estado():
    est = FakeEstado()
    est.store["job-1"] = {
        "job_id": "job-1", "estado": "ok", "slug": "pr",
        "resumen": "provision_manifest: pr OK -> ", "empresa_id": 7,
    }
    async with _cli(_app(est=est)) as c:
        r = await c.get("/api/v1/admin/jobs/job-1", headers=_plataforma())
    assert r.status_code == 200
    b = r.json()
    assert b["estado"] == "ok" and b["slug"] == "pr" and b["empresa_id"] == 7
    assert b["resumen"].startswith("provision_manifest:")


async def test_get_job_inexistente_404():
    async with _cli(_app(est=FakeEstado())) as c:
        r = await c.get("/api/v1/admin/jobs/nope", headers=_plataforma())
    assert r.status_code == 404


# ── PUT /admin/tenants/{slug}/features ───────────────────────────────────────

async def test_put_feature_desconocida_400():
    # Catálogo: 'no_existe' no es una feature → set_feature lanza ANTES de tocar la BD → 400.
    async with _cli(_app()) as c:
        r = await c.put(
            "/api/v1/admin/tenants/pr/features",
            json={"feature": "no_existe", "habilitada": True}, headers=_plataforma(),
        )
    assert r.status_code == 400


async def test_put_feature_valida_toggle(monkeypatch):
    name, url = _control_efimero(monkeypatch)
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        with psycopg.connect(to_libpq(url)) as conn:
            plan_id = conn.execute(
                "INSERT INTO planes (nombre, limites) VALUES ('Núcleo','{\"features\": [\"pos\"]}') RETURNING id"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO empresas (nombre,nit,slug,estado,plan_id) VALUES ('PR','NIT-PR','pr','activa',%s)",
                (plan_id,),
            )
            conn.commit()
        async with _cli(_app()) as c:
            r = await c.put(
                "/api/v1/admin/tenants/pr/features",
                json={"feature": "fiados", "habilitada": True}, headers=_plataforma(),
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slug"] == "pr"
        assert "fiados" in body["features"] and "pos" in body["features"]
    finally:
        get_settings.cache_clear()
        drop_database(name)


# ── POST /admin/tenants/{slug}/identidad-admin ───────────────────────────────

async def test_post_identidad_admin_email_invalido_422():
    async with _cli(_app()) as c:
        r = await c.post(
            "/api/v1/admin/tenants/pr/identidad-admin",
            json={"email": "no-es-un-email"}, headers=_plataforma(),
        )
    assert r.status_code == 422


async def test_post_identidad_admin_crea_identidad(monkeypatch):
    name, url = _control_efimero(monkeypatch)
    slug = f"idadm{uuid.uuid4().hex[:8]}"
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        provision_tenant_full({
            "slug": slug, "nombre": "Mini", "nit": f"NIT-{uuid.uuid4().hex[:8]}",
            "admin": {"nombre": "Admin"},
        })
        async with _cli(_app()) as c:
            r = await c.post(
                f"/api/v1/admin/tenants/{slug}/identidad-admin",
                json={"email": "Dueno@Mini.CO"}, headers=_plataforma(),
            )
        assert r.status_code == 200, r.text
        assert r.json()["identidad_id"] > 0
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as cc:
            row = cc.execute(
                "SELECT rol FROM identidades WHERE lower(email)=%s", ("dueno@mini.co",)
            ).fetchone()
        assert row is not None and row["rol"] == "admin"
    finally:
        drop_database(_db_name(slug))
        get_settings.cache_clear()
        drop_database(name)


# ── Gate: un NO-plataforma (admin/vendedor) → 403 en todas ───────────────────

async def test_no_plataforma_403_en_todas_las_rutas():
    tok = {"Authorization": f"Bearer {create_access_token(user_id=1, tenant='pr', rol='admin')}"}
    async with _cli(_app(FakeEnqueuer(), FakeEstado())) as c:
        r_post = await c.post("/api/v1/admin/tenants", json=_MANIFIESTO_OK, headers=tok)
        r_job = await c.get("/api/v1/admin/jobs/x", headers=tok)
        r_feat = await c.put(
            "/api/v1/admin/tenants/pr/features", json={"feature": "fiados", "habilitada": True}, headers=tok
        )
        r_ident = await c.post(
            "/api/v1/admin/tenants/pr/identidad-admin", json={"email": "a@b.co"}, headers=tok
        )
    assert [r_post.status_code, r_job.status_code, r_feat.status_code, r_ident.status_code] == [403, 403, 403, 403]
