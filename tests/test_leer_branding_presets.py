"""`leer_branding` contra un control DB efímero: resuelve preset+overrides a tokens (plan §5.2).

Patrón de test_provision_from_manifest (control DB efímero migrado a head). Cubre: que la migración
0009 añadió `branding.preset`; que un tenant con `preset` nace con los tokens de su gremio; que un
`color_primario` explícito GANA sobre el preset (NO-REGRESIÓN Punto Rojo: sigue #C8200E); que un
tenant sin fila hereda el default Melquiadez; y que `tema` (nombre viejo) sirve de fallback del preset.
"""
import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import core.db.session as session_mod
from core.config import get_settings
from core.db.urls import tenant_url, to_async, to_libpq
from core.tenancy.branding_presets import PRESETS
from core.tenancy.control_repo import leer_branding
from tests.conftest import create_database, drop_database


@pytest.fixture
async def control_db(monkeypatch):
    """Control DB efímero migrado a head; devuelve la URL base. Limpia el engine global de sesión."""
    name = f"test_control_brand_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        yield url
    finally:
        get_settings.cache_clear()
        drop_database(name)


def _insert_empresa(conn, *, slug: str) -> int:
    return conn.execute(
        "INSERT INTO empresas (nombre, nit, slug, estado) VALUES (%s,%s,%s,'activa') RETURNING id",
        (slug, f"NIT-{uuid.uuid4().hex[:8]}", slug),
    ).fetchone()["id"]


async def _leer(url: str, empresa_id: int) -> dict:
    engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        async with AsyncSession(engine) as s:
            return await leer_branding(s, empresa_id)
    finally:
        await engine.dispose()


async def test_migracion_agrega_columna_preset(control_db):
    with psycopg.connect(to_libpq(control_db), row_factory=dict_row) as conn:
        cols = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'branding'"
            ).fetchall()
        }
    assert "preset" in cols


async def test_preset_navaja_resuelve_sus_tokens(control_db):
    with psycopg.connect(to_libpq(control_db), autocommit=True, row_factory=dict_row) as conn:
        eid = _insert_empresa(conn, slug=f"barb{uuid.uuid4().hex[:8]}")
        conn.execute(
            "INSERT INTO branding (empresa_id, preset) VALUES (%s, 'navaja')", (eid,)
        )
    resuelto = await _leer(control_db, eid)
    navaja = PRESETS["navaja"].tokens()
    assert resuelto["preset"] == "navaja"
    assert resuelto["tokens"] == navaja                      # sin override → tokens puros del preset
    assert resuelto["color_primario"] == navaja["primario"]  # compat


async def test_color_explicito_gana_no_regresion_punto_rojo(control_db):
    with psycopg.connect(to_libpq(control_db), autocommit=True, row_factory=dict_row) as conn:
        eid = _insert_empresa(conn, slug=f"pr{uuid.uuid4().hex[:8]}")
        # Fila estilo Punto Rojo: color explícito, sin preset.
        conn.execute(
            "INSERT INTO branding (empresa_id, color_primario) VALUES (%s, '#C8200E')", (eid,)
        )
    resuelto = await _leer(control_db, eid)
    assert resuelto["color_primario"] == "#C8200E"
    assert resuelto["tokens"]["primario"] == "#C8200E"        # el explícito gana
    assert resuelto["tokens"]["superficie"] == PRESETS["melquiadez"].tokens()["superficie"]


async def test_sin_fila_hereda_default_melquiadez(control_db):
    with psycopg.connect(to_libpq(control_db), autocommit=True, row_factory=dict_row) as conn:
        eid = _insert_empresa(conn, slug=f"x{uuid.uuid4().hex[:8]}")  # sin fila branding
    resuelto = await _leer(control_db, eid)
    assert resuelto["preset"] == "melquiadez"
    assert resuelto["color_primario"] == PRESETS["melquiadez"].tokens()["primario"]


async def test_tema_legacy_es_fallback_del_preset(control_db):
    with psycopg.connect(to_libpq(control_db), autocommit=True, row_factory=dict_row) as conn:
        eid = _insert_empresa(conn, slug=f"cli{uuid.uuid4().hex[:8]}")
        # Fila vieja: tema seteado, preset NULL → el preset se resuelve desde tema.
        conn.execute(
            "INSERT INTO branding (empresa_id, tema) VALUES (%s, 'aurora')", (eid,)
        )
    resuelto = await _leer(control_db, eid)
    assert resuelto["preset"] == "aurora"
    assert resuelto["tema"] == "aurora"                       # passthrough legacy
    assert resuelto["tokens"]["primario"] == PRESETS["aurora"].tokens()["primario"]
