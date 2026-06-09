"""Migración de control 0004: grandfather EXPLÍCITO del pack `pos` (ADR 0008 §D4). Requiere Postgres.

Siembra tenants en la revisión 0003 y aplica 0004; verifica que `pos` se activa SOLO para el retail
conocido (`puntorojo`) y NO para `clinica-demo` ni para un tenant desconocido (la migración no adivina:
lo deja para revisión manual, sin fallar). Control DB efímero (patrón test_set_feature).
"""
import uuid

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from tests.conftest import create_database, drop_database

_CFG = Config("migrations/control/alembic.ini")


def _pos_activo(conn, slug: str) -> bool:
    row = conn.execute(
        "SELECT ef.habilitada FROM empresa_features ef "
        "JOIN empresas e ON e.id = ef.empresa_id "
        "WHERE e.slug = %s AND ef.feature = 'pos'",
        (slug,),
    ).fetchone()
    return bool(row and row["habilitada"])


async def test_grandfather_pos_explicito(monkeypatch):
    name = f"test_control_gf_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()

    create_database(name)
    try:
        # Estado PREVIO al grandfather: esquema en 0003, con los tenants ya creados.
        command.upgrade(_CFG, "0003_wa_numeros")
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
            for slug in ("puntorojo", "clinica-demo", "otra-ferreteria"):
                conn.execute(
                    "INSERT INTO empresas (nombre, nit, slug, estado) VALUES (%s,%s,%s,'activa')",
                    (slug, f"NIT-{slug}", slug),
                )
            conn.commit()

        # Aplica el grandfather.
        command.upgrade(_CFG, "0004_grandfather_pos")

        with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
            assert _pos_activo(conn, "puntorojo") is True        # retail conocido → pos
            assert _pos_activo(conn, "clinica-demo") is False     # servicios → NO pos
            assert _pos_activo(conn, "otra-ferreteria") is False  # desconocido → no se adivina

        # Idempotente: re-aplicar (downgrade→upgrade) no duplica ni cambia el resultado.
        command.downgrade(_CFG, "0003_wa_numeros")
        command.upgrade(_CFG, "0004_grandfather_pos")
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
            assert _pos_activo(conn, "puntorojo") is True
            n = conn.execute(
                "SELECT count(*) AS n FROM empresa_features WHERE feature='pos'"
            ).fetchone()["n"]
            assert n == 1                                         # una sola fila (solo puntorojo)
    finally:
        get_settings.cache_clear()
        drop_database(name)
