"""Helper de operación `tools.set_feature` (encender/apagar una feature de un tenant en el control DB).

La validación de catálogo (feature desconocida / de núcleo) ocurre ANTES de tocar la BD, así que esos
casos no necesitan Postgres. El camino feliz (UPSERT idempotente + set efectivo + rechazo por
dependencia) corre contra un control DB efímero, como `test_provision_tenant`.
"""
import json
import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg.rows import dict_row

from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from tests.conftest import create_database, drop_database
from tools.set_feature import set_feature


# --- validación de catálogo (sin Postgres: falla antes de conectar) ---------
def test_feature_desconocida_falla():
    with pytest.raises(ValueError, match="desconocida"):
        set_feature("cualquier", "no_existe", True)


def test_feature_de_nucleo_falla():
    with pytest.raises(ValueError, match="núcleo"):
        set_feature("cualquier", "clientes", True)   # 'clientes' es núcleo: no se togglea


# --- camino feliz contra control DB efímero ---------------------------------
@pytest.fixture
def control_db(monkeypatch):
    """Control DB efímero migrado a head; siembra un plan + una empresa y devuelve (slug, url)."""
    name = f"test_control_feat_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()

    slug = f"feat{uuid.uuid4().hex[:10]}"
    create_database(name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
            plan_id = conn.execute(
                "INSERT INTO planes (nombre, limites) VALUES (%s, CAST(%s AS JSONB)) RETURNING id",
                (f"Plan-{slug}", json.dumps({"features": ["facturacion_electronica"]})),
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO empresas (nombre, nit, slug, estado, plan_id) VALUES (%s,%s,%s,'activa',%s)",
                ("Prueba", f"NIT-{slug}", slug, plan_id),
            )
            conn.commit()
        yield slug, url
    finally:
        get_settings.cache_clear()
        drop_database(name)


def _overrides(url: str, slug: str) -> list[tuple[str, bool]]:
    with psycopg.connect(to_libpq(url), row_factory=dict_row) as conn:
        empresa_id = conn.execute("SELECT id FROM empresas WHERE slug=%s", (slug,)).fetchone()["id"]
        filas = conn.execute(
            "SELECT feature, habilitada FROM empresa_features WHERE empresa_id=%s ORDER BY feature",
            (empresa_id,),
        ).fetchall()
        return [(f["feature"], f["habilitada"]) for f in filas]


def test_activar_desactivar_idempotente_y_efectivas(control_db):
    slug, url = control_db

    # Activar pack_faq → en el set efectivo (con núcleo y la feature del plan).
    efectivas = set_feature(slug, "pack_faq", True)
    assert "pack_faq" in efectivas
    assert "facturacion_electronica" in efectivas   # del plan
    assert "clientes" in efectivas                   # núcleo (ADR 0008: ya no es 'ventas')
    assert _overrides(url, slug) == [("pack_faq", True)]

    # Idempotente: re-activar no duplica la fila.
    set_feature(slug, "pack_faq", True)
    assert _overrides(url, slug) == [("pack_faq", True)]

    # Desactivar → sale del set efectivo; sigue una sola fila (UPSERT, habilitada=false).
    efectivas = set_feature(slug, "pack_faq", False)
    assert "pack_faq" not in efectivas
    assert _overrides(url, slug) == [("pack_faq", False)]


def test_rechazo_por_dependencia_no_escribe(control_db):
    slug, url = control_db
    # ventas_voz requiere bot_telegram (no presente) → ValueError y NO se escribe override.
    with pytest.raises(ValueError, match="dependencias"):
        set_feature(slug, "ventas_voz", True)
    assert _overrides(url, slug) == []
