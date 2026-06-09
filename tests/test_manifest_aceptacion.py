"""Aceptación ADR 0007 §D6: el manifiesto canónico reconstruye clinica-demo IDÉNTICA al seed bespoke.

Siembra una BD con el seed bespoke (`seed_clinica_demo.seed_agenda`) y otra con el camino del
manifiesto (`provision_from_manifest` sobre `clinica-demo.manifest.example.yaml`), y afirma que las
filas de agenda quedan equivalentes (servicios, recursos, recurso_servicio, disponibilidad,
agenda_config; conteos + campos clave). NO compara `conocimiento` (el seed bespoke no siembra FAQ).

Esta prueba es la definición de "el onboarding ya es declarativo": si pasa, el seed bespoke se deprecó.
Usa un slug ÚNICO (no toca el ferrebot_clinica-demo real del entorno de dev).
"""
import tempfile
import uuid
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

import core.db.session as session_mod
from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from core.tenancy.cache import control_cache
from tests.conftest import create_database, drop_database
from tools.provision_from_manifest import provision_from_manifest
from tools.provision_tenant import _db_name
from tools.seed_clinica_demo import seed_agenda

_CANONICO = Path(__file__).parents[1] / "tools" / "onboarding" / "clinica-demo.manifest.example.yaml"

# Campos de agenda_config que AMBOS caminos fijan explícitamente (los comparables).
_CONFIG_KEYS = (
    "zona_horaria", "intervalo_slots_min", "anticipacion_minima_min", "ventana_maxima_dias",
    "politica_cancelacion_horas", "corte_riesgo_horas", "permite_reagendar", "modo_confirmacion",
    "requiere_anticipo", "capacidad_por_slot", "recordatorios_horas", "persona",
)


def _snapshot_agenda(conn) -> dict:
    """Normaliza las filas de agenda a estructuras comparables (independientes de los ids)."""
    servicios = {
        r["nombre"]: (r["duracion_min"], r["precio"], r["buffer_antes_min"],
                      r["buffer_despues_min"], r["categoria"])
        for r in conn.execute(
            "SELECT nombre, duracion_min, precio, buffer_antes_min, buffer_despues_min, categoria "
            "FROM servicios"
        ).fetchall()
    }
    recursos = {
        r["nombre"]: r["tipo"]
        for r in conn.execute("SELECT nombre, tipo FROM recursos").fetchall()
    }
    asignaciones = {
        (r["recurso"], r["servicio"])
        for r in conn.execute(
            "SELECT rc.nombre AS recurso, s.nombre AS servicio FROM recurso_servicio rs "
            "JOIN recursos rc ON rc.id = rs.recurso_id JOIN servicios s ON s.id = rs.servicio_id"
        ).fetchall()
    }
    disponibilidad = {
        (r["recurso"], r["dia_semana"], r["hora_inicio"], r["hora_fin"])
        for r in conn.execute(
            "SELECT rc.nombre AS recurso, d.dia_semana, d.hora_inicio, d.hora_fin "
            "FROM disponibilidad d JOIN recursos rc ON rc.id = d.recurso_id"
        ).fetchall()
    }
    fila = conn.execute(
        f"SELECT {', '.join(_CONFIG_KEYS)} FROM agenda_config WHERE id = 1"
    ).fetchone()
    return {
        "servicios": servicios,
        "recursos": recursos,
        "asignaciones": asignaciones,
        "disponibilidad": disponibilidad,
        "config": {k: fila[k] for k in _CONFIG_KEYS},
    }


async def test_manifiesto_canonico_equivale_al_seed_bespoke(tenant, monkeypatch):
    # --- BD bespoke: el seed hardcodeado siembra la agenda en una BD efímera migrada (fixture). ---
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        seed_agenda(tenant.url)  # abre su propia conexión; commitea
        bespoke = _snapshot_agenda(conn)

    # --- BD manifiesto: provision_from_manifest sobre el canónico, con slug único (no toca dev). ---
    control_name = f"test_control_acc_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)

    slug = f"clinacc{uuid.uuid4().hex[:10]}"
    control_cache.invalidate(slug)
    texto = (
        _CANONICO.read_text(encoding="utf-8")
        .replace("slug: clinica-demo", f"slug: {slug}")
        .replace('nit: "900999999-9"', f'nit: "NIT-{uuid.uuid4().hex[:8]}"')
        .replace('phone_number_id: "1176767388843502"', f'phone_number_id: "{uuid.uuid4().hex[:15]}"')
    )
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        fh.write(texto)
        manifiesto_path = fh.name

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        provision_from_manifest(manifiesto_path)

        manifest_db_url = tenant_url(get_settings().tenants_direct_url_base, _db_name(slug))
        with psycopg.connect(to_libpq(manifest_db_url), row_factory=dict_row) as conn:
            desde_manifiesto = _snapshot_agenda(conn)

        # --- Equivalencia: mismas filas de agenda por ambos caminos. ---
        assert desde_manifiesto["servicios"] == bespoke["servicios"]
        assert desde_manifiesto["recursos"] == bespoke["recursos"]
        assert desde_manifiesto["asignaciones"] == bespoke["asignaciones"]
        assert desde_manifiesto["disponibilidad"] == bespoke["disponibilidad"]
        assert desde_manifiesto["config"] == bespoke["config"]
        # Sanidad de los conteos esperados (clinica-demo).
        assert len(bespoke["servicios"]) == 3
        assert len(bespoke["recursos"]) == 2
        assert len(bespoke["asignaciones"]) == 3
        assert len(bespoke["disponibilidad"]) == 20
        assert bespoke["config"]["modo_confirmacion"] == "manual"
    finally:
        Path(manifiesto_path).unlink(missing_ok=True)
        drop_database(_db_name(slug))
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate(slug)
        get_settings.cache_clear()
        drop_database(control_name)
