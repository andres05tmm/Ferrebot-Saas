"""Migración de control 0006 (identidad de plataforma): empresa_id nullable + CHECK (ADR 0010). Postgres.

El CHECK `ck_identidades_rol_empresa` ata la nulabilidad de `empresa_id` al rol: super_admin ⇒ sin
empresa; admin/vendedor ⇒ con empresa. Verifica el CHECK en ambos sentidos y el downgrade limpio
(borra las identidades de plataforma y restaura empresa_id NOT NULL).
"""
import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from tests.conftest import create_database, drop_database

_CFG = Config("migrations/control/alembic.ini")


def _ins(conn, email, empresa_id, usuario_id, rol):
    conn.execute(
        "INSERT INTO identidades (email, empresa_id, usuario_id, rol) VALUES (%s,%s,%s,%s)",
        (email, empresa_id, usuario_id, rol),
    )


async def test_check_rol_empresa_y_downgrade_limpio(monkeypatch):
    name = f"test_control_mig6_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()

    create_database(name)
    try:
        command.upgrade(_CFG, "head")
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
            eid = conn.execute(
                "INSERT INTO empresas (nombre, nit, slug, estado) VALUES ('E','NIT','e','activa') RETURNING id"
            ).fetchone()["id"]

            # super_admin SIN empresa → permitido.
            _ins(conn, "sa@x.co", None, 0, "super_admin")
            conn.commit()
            # super_admin CON empresa → viola el CHECK.
            with pytest.raises(psycopg.errors.CheckViolation):
                _ins(conn, "sa2@x.co", eid, 0, "super_admin")
            conn.rollback()
            # admin SIN empresa → viola el CHECK.
            with pytest.raises(psycopg.errors.CheckViolation):
                _ins(conn, "ad@x.co", None, 1, "admin")
            conn.rollback()
            # admin CON empresa → permitido.
            _ins(conn, "ad@x.co", eid, 1, "admin")
            conn.commit()

        # Downgrade a 0005: borra la identidad de plataforma (empresa_id NULL) y restaura NOT NULL.
        command.downgrade(_CFG, "0005_identidades")
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
            n = conn.execute("SELECT count(*) AS n FROM identidades").fetchone()["n"]
            assert n == 1                                  # sobrevive solo la identidad de tenant (admin)
            with pytest.raises(psycopg.errors.NotNullViolation):
                _ins(conn, "z@x.co", None, 0, "super_admin")   # empresa_id volvió a NOT NULL
            conn.rollback()
    finally:
        get_settings.cache_clear()
        drop_database(name)
