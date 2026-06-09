"""Migración de control 0005 (identidades): upgrade/downgrade limpio (ADR 0009). Requiere Postgres.

Verifica que la tabla `identidades` y su índice único por `lower(email)` se crean en upgrade y se
retiran en downgrade, y que la unicidad case-insensitive del email la hace cumplir el índice.
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


def _tabla_existe(conn, tabla: str) -> bool:
    row = conn.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS existe", (f"public.{tabla}",)
    ).fetchone()
    return row["existe"]


async def test_identidades_upgrade_downgrade_y_unicidad(monkeypatch):
    name = f"test_control_mig5_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()

    create_database(name)
    try:
        command.upgrade(_CFG, "head")
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
            assert _tabla_existe(conn, "identidades") is True
            eid = conn.execute(
                "INSERT INTO empresas (nombre, nit, slug, estado) "
                "VALUES ('E','NIT','e','activa') RETURNING id"
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO identidades (email, empresa_id, usuario_id, rol) VALUES ('a@x.co',%s,1,'admin')",
                (eid,),
            )
            conn.commit()
            # Unicidad case-insensitive: otro casing del mismo email viola el índice lower(email).
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "INSERT INTO identidades (email, empresa_id, usuario_id, rol) VALUES ('A@X.CO',%s,2,'vendedor')",
                    (eid,),
                )
            conn.rollback()

        # Downgrade retira la tabla limpio.
        command.downgrade(_CFG, "0004_grandfather_pos")
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
            assert _tabla_existe(conn, "identidades") is False
    finally:
        get_settings.cache_clear()
        drop_database(name)
