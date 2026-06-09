"""Grandfather de la primera identidad super-admin (ADR 0010 §D2). Requiere Postgres.

Siembra una identidad de PLATAFORMA (empresa_id NULL, rol super_admin, sin contraseña) idempotente por
email. No depende de ningún tenant.
"""
import uuid

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from tests.conftest import create_database, drop_database
from tools.grandfather_superadmin import grandfather_superadmin


def _identidad(url, email):
    with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
        return conn.execute(
            "SELECT id, empresa_id, usuario_id, rol, password_hash FROM identidades WHERE lower(email)=%s",
            (email.lower(),),
        ).fetchall()


async def test_grandfather_superadmin_crea_identidad_plataforma_idempotente(monkeypatch):
    name = f"test_control_gfsa_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()

    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")

        id1, _token = grandfather_superadmin("Andres@FerreBot.CO")
        filas = _identidad(url, "andres@ferrebot.co")
        assert len(filas) == 1
        f = filas[0]
        assert f["empresa_id"] is None                 # identidad de plataforma: sin empresa
        assert f["rol"] == "super_admin"
        assert f["password_hash"] is None              # sin clave: pendiente de set-password

        # Idempotente: re-correr (otro casing) no duplica ni cambia el id.
        id2, _ = grandfather_superadmin("andres@ferrebot.co")
        assert id2 == id1
        assert len(_identidad(url, "andres@ferrebot.co")) == 1
    finally:
        get_settings.cache_clear()
        drop_database(name)
