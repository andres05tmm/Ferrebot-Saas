"""Identidad de login en el provisionador + grandfather (login real, ADR 0009 A1.4). Requiere Postgres.

E2E contra control DB efímero + app DB efímera (patrón test_provision_from_manifest):
- provisionar desde un manifiesto con `admin.email` crea EXACTAMENTE una identidad ligada al usuario
  admin del tenant (password_hash NULL); re-provisionar no duplica.
- el grandfather siembra la identidad de un tenant existente (sin email en su alta) a partir de su
  admin, sin tocar la data de negocio; idempotente.
"""
import uuid

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

import core.db.session as session_mod
from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from core.tenancy.cache import control_cache
from tests.conftest import create_database, drop_database
from tools.grandfather_identidad import grandfather
from tools.provision_from_manifest import provision_from_manifest
from tools.provision_tenant import _db_name

_MANIFIESTO = """\
version: 1
identidad:
  slug: {slug}
  nombre: "Mini SAS"
  nit: "{nit}"
admin:
  nombre: "Admin Mini"
{email_linea}
plan:
  nombre: "Núcleo"
  features: []
"""

_MANIFIESTO_DEMO = """\
version: 1
identidad:
  slug: {slug}
  nombre: "Demo SAS"
  nit: "{nit}"
admin:
  nombre: "Admin Demo"
  email: "admin@{slug}.co"
identidades:
  - email: "demo+{slug}@melquiadez.com"
    nombre: "Visitante Demo"
    rol: "vendedor"
plan:
  nombre: "Núcleo"
  features: []
"""


def _escribir_manifiesto(tmp_path, slug, *, email: str | None):
    email_linea = f'  email: "{email}"' if email else ""
    texto = _MANIFIESTO.format(slug=slug, nit=f"NIT-{uuid.uuid4().hex[:8]}", email_linea=email_linea)
    ruta = tmp_path / f"{slug}.yaml"
    ruta.write_text(texto, encoding="utf-8")
    return str(ruta)


def _escribir_manifiesto_demo(tmp_path, slug):
    texto = _MANIFIESTO_DEMO.format(slug=slug, nit=f"NIT-{uuid.uuid4().hex[:8]}")
    ruta = tmp_path / f"{slug}.yaml"
    ruta.write_text(texto, encoding="utf-8")
    return str(ruta)


def _identidades(control_url, slug):
    with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as cc:
        return cc.execute(
            "SELECT i.email, i.password_hash, i.usuario_id, i.rol "
            "FROM identidades i JOIN empresas e ON e.id = i.empresa_id WHERE e.slug = %s",
            (slug,),
        ).fetchall()


def _admin_id_tenant(slug):
    url = tenant_url(get_settings().tenants_direct_url_base, _db_name(slug))
    with psycopg.connect(to_libpq(url), row_factory=dict_row) as tc:
        return tc.execute("SELECT id FROM usuarios WHERE rol='admin' ORDER BY id LIMIT 1").fetchone()["id"]


def _n_usuarios_tenant(slug):
    url = tenant_url(get_settings().tenants_direct_url_base, _db_name(slug))
    with psycopg.connect(to_libpq(url), row_factory=dict_row) as tc:
        return tc.execute("SELECT count(*) AS n FROM usuarios").fetchone()["n"]


def _control_efimero(monkeypatch):
    control_name = f"test_control_ident_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)
    return control_name, control_url


async def test_provisionar_con_admin_email_crea_identidad_idempotente(tmp_path, monkeypatch):
    control_name, control_url = _control_efimero(monkeypatch)
    slug = f"mid{uuid.uuid4().hex[:10]}"
    control_cache.invalidate(slug)
    manifiesto = _escribir_manifiesto(tmp_path, slug, email="Admin@Mini.CO")




    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        provision_from_manifest(manifiesto)

        filas = _identidades(control_url, slug)
        assert len(filas) == 1
        ident = filas[0]
        assert ident["email"] == "admin@mini.co"            # normalizado a minúsculas
        assert ident["password_hash"] is None                # sin clave: pendiente de set-password
        assert ident["rol"] == "admin"
        assert ident["usuario_id"] == _admin_id_tenant(slug)  # ligada al admin del tenant

        # Re-provisionar NO duplica (upsert por email).
        provision_from_manifest(manifiesto)
        assert len(_identidades(control_url, slug)) == 1
    finally:
        drop_database(_db_name(slug))
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate(slug)
        get_settings.cache_clear()
        drop_database(control_name)


async def test_provisionar_identidad_demo_no_admin_idempotente(tmp_path, monkeypatch):
    # Un tenant demo: admin + una identidad DEMO no-admin (rol vendedor). Se crean DOS identidades y
    # DOS usuarios en el tenant (admin + vendedor); re-provisionar no duplica ninguno.
    control_name, control_url = _control_efimero(monkeypatch)
    slug = f"dmo{uuid.uuid4().hex[:9]}"
    control_cache.invalidate(slug)
    manifiesto = _escribir_manifiesto_demo(tmp_path, slug)

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        provision_from_manifest(manifiesto)

        filas = {f["email"]: f for f in _identidades(control_url, slug)}
        assert set(filas) == {f"admin@{slug}.co", f"demo+{slug}@melquiadez.com"}
        demo = filas[f"demo+{slug}@melquiadez.com"]
        assert demo["rol"] == "vendedor"
        assert demo["password_hash"] is None                 # pendiente de set-password
        # La identidad demo apunta a un usuario `vendedor` del tenant, NO al admin.
        assert demo["usuario_id"] != _admin_id_tenant(slug)
        url = tenant_url(get_settings().tenants_direct_url_base, _db_name(slug))
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as tc:
            rol_demo = tc.execute(
                "SELECT rol FROM usuarios WHERE id = %s", (demo["usuario_id"],)
            ).fetchone()["rol"]
        assert rol_demo == "vendedor"
        assert _n_usuarios_tenant(slug) == 2                 # admin + vendedor demo

        # Re-provisionar NO duplica: mismas 2 identidades y mismos 2 usuarios.
        provision_from_manifest(manifiesto)
        assert len(_identidades(control_url, slug)) == 2
        assert _n_usuarios_tenant(slug) == 2
    finally:
        drop_database(_db_name(slug))
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate(slug)
        get_settings.cache_clear()
        drop_database(control_name)


async def test_grandfather_siembra_identidad_sin_tocar_negocio(tmp_path, monkeypatch):
    control_name, control_url = _control_efimero(monkeypatch)
    slug = f"gfd{uuid.uuid4().hex[:10]}"
    control_cache.invalidate(slug)
    manifiesto = _escribir_manifiesto(tmp_path, slug, email=None)   # alta SIN email → sin identidad




    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        provision_from_manifest(manifiesto)
        assert _identidades(control_url, slug) == []           # sin admin.email → sin identidad
        usuarios_antes = _n_usuarios_tenant(slug)

        # Grandfather: siembra la identidad con el email recibido.
        identidad_id, _token = grandfather(slug, "Dueno@Gfd.CO")
        assert identidad_id is not None
        filas = _identidades(control_url, slug)
        assert len(filas) == 1 and filas[0]["email"] == "dueno@gfd.co"
        assert filas[0]["usuario_id"] == _admin_id_tenant(slug)

        # Idempotente y sin tocar la data de negocio del tenant.
        grandfather(slug, "Dueno@Gfd.CO")
        assert len(_identidades(control_url, slug)) == 1
        assert _n_usuarios_tenant(slug) == usuarios_antes
    finally:
        drop_database(_db_name(slug))
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate(slug)
        get_settings.cache_clear()
        drop_database(control_name)
