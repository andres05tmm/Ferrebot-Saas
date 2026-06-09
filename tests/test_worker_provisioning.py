"""Job de provisioning del panel super-admin (apps.worker.jobs.provisionar_tenant, ADR 0010 §B2).

Integración con BD efímera (Postgres) + Redis real. Cubre los guardarraíles no negociables:
- aprovisiona desde un manifiesto DICT y deja estado=ok + resumen (la línea del provisionador);
- slug inválido → estado=error SIN tocar la BD (no crea empresa);
- dos jobs del mismo slug se SERIALIZAN por el lock (no hay doble CREATE DATABASE en carrera);
- el camino de error guarda un mensaje SANITIZADO y un secreto del manifiesto NO aparece en el
  estado/error ni en los logs.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row
from structlog.testing import capture_logs

from apps.worker.jobs import EstadoProvision, _cliente_redis, provisionar_tenant
from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from tests.conftest import create_database, drop_database
from tools.provision_tenant import _db_name


def _control_efimero(monkeypatch) -> tuple[str, str]:
    name = f"test_control_prov_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    return name, url


def _manifiesto(slug: str, *, nit: str | None = None, secretos: dict | None = None) -> dict:
    m: dict = {
        "version": 1,
        "identidad": {"slug": slug, "nombre": "Mini", "nit": nit or f"NIT-{uuid.uuid4().hex[:8]}"},
        "admin": {"nombre": "Admin"},
    }
    if secretos is not None:
        m["secretos"] = secretos
    return m


async def _estado(job_id: str) -> dict | None:
    redis = _cliente_redis(get_settings().redis_url)
    try:
        return await EstadoProvision(redis, 3600).obtener(job_id)
    finally:
        await redis.aclose()


# --- camino feliz ------------------------------------------------------------

async def test_job_aprovisiona_y_estado_ok(monkeypatch):
    name, control_url = _control_efimero(monkeypatch)
    slug = f"mini{uuid.uuid4().hex[:10]}"
    job_id = uuid.uuid4().hex
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        rc = await provisionar_tenant({}, _manifiesto(slug), job_id)
        assert rc == "ok"

        with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as cc:
            row = cc.execute("SELECT id FROM empresas WHERE slug=%s", (slug,)).fetchone()
        assert row is not None

        est = await _estado(job_id)
        assert est["estado"] == "ok"
        assert est["slug"] == slug
        assert est["empresa_id"] == row["id"]
        assert est["resumen"].startswith("provision_manifest:")   # la línea del provisionador
        assert "error" not in est
    finally:
        drop_database(_db_name(slug))
        get_settings.cache_clear()
        drop_database(name)


# --- slug inválido: falla ANTES de tocar la BD -------------------------------

async def test_slug_invalido_estado_error_sin_tocar_bd(monkeypatch):
    name, control_url = _control_efimero(monkeypatch)
    job_id = uuid.uuid4().hex
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        # 'Bad_Slug!' viola el patrón estricto (mayúsculas, guion bajo, signo).
        rc = await provisionar_tenant({}, _manifiesto("Bad_Slug!"), job_id)
        assert rc == "error"

        with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as cc:
            assert cc.execute("SELECT count(*) AS n FROM empresas").fetchone()["n"] == 0   # no tocó BD

        est = await _estado(job_id)
        assert est["estado"] == "error"
        assert est["error"] == "slug inválido"
    finally:
        get_settings.cache_clear()
        drop_database(name)


# --- serialización por lock --------------------------------------------------

async def test_dos_jobs_mismo_slug_se_serializan(monkeypatch):
    name, control_url = _control_efimero(monkeypatch)
    slug = f"race{uuid.uuid4().hex[:10]}"
    m = _manifiesto(slug)
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        # Dos jobs del MISMO slug a la vez: el lock serializa el CREATE DATABASE (sin carrera) → ambos ok.
        r1, r2 = await asyncio.gather(
            provisionar_tenant({}, m, uuid.uuid4().hex),
            provisionar_tenant({}, m, uuid.uuid4().hex),
        )
        assert {r1, r2} == {"ok"}

        with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as cc:
            n = cc.execute("SELECT count(*) AS n FROM empresas WHERE slug=%s", (slug,)).fetchone()["n"]
        assert n == 1   # una sola empresa: idempotente, sin doble alta
    finally:
        drop_database(_db_name(slug))
        get_settings.cache_clear()
        drop_database(name)


# --- disciplina de secretos: nunca en estado/error/logs ----------------------

async def test_secreto_no_aparece_en_error_estado_ni_logs(monkeypatch):
    secreto = "SUPER-SECRETO-NUNCA-LOGUEAR-9z9z"
    slug = f"leak{uuid.uuid4().hex[:10]}"
    job_id = uuid.uuid4().hex
    m = _manifiesto(slug, secretos={"matias_password": secreto})

    # Peor caso: el provisionador falla con un error que ARRASTRA el secreto (p. ej. una URL de conexión
    # con clave en un error de capa baja). El job NO debe propagarlo al estado/error ni a los logs.
    def _fake_obj(manifiesto, *, on_resumen=None):
        raise RuntimeError(f"conn failed: postgres://u:{secreto}@host/db")

    monkeypatch.setattr("apps.worker.jobs.provision_from_manifest_obj", _fake_obj)

    with capture_logs() as logs:
        rc = await provisionar_tenant({}, m, job_id)
    assert rc == "error"

    est = await _estado(job_id)
    assert est["estado"] == "error"
    assert est["error"] == "fallo de provisioning"      # sanitizado: categoría, no el texto crudo
    assert secreto not in json.dumps(est)               # el secreto no está en el estado
    assert secreto not in json.dumps(logs)              # ni en los logs
