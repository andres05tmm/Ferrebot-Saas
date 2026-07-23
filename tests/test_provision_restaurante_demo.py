"""Provisionar el restaurante-demo DESDE CERO termina con smoke verde (F7 / ADR 0032, DoD global).

Usa el manifiesto REAL (`tools/onboarding/restaurante-demo.manifest.example.yaml`) con slug/nit/plan
reescritos a valores únicos de test (jamás toca una demo local real). Control DB efímero (patrón
test_provision_from_manifest). Smoke: catálogo con modificadores/recetas/zonas KDS/mesas/recargo +
flags nuevos del pack restaurante en el plan + idempotencia E2E (re-correr no duplica).
"""
import uuid
from pathlib import Path

import psycopg
import yaml
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

_PLANTILLA = Path(__file__).parents[1] / "tools" / "onboarding" / "restaurante-demo.manifest.example.yaml"


async def test_provision_restaurante_demo_smoke(tmp_path, monkeypatch):
    control_name = f"test_control_resto_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)

    # El manifiesto REAL con identidad única de test (mismo contenido de packs).
    datos = yaml.safe_load(_PLANTILLA.read_text(encoding="utf-8"))
    slug = f"resto{uuid.uuid4().hex[:10]}"
    datos["identidad"]["slug"] = slug
    datos["identidad"]["nit"] = f"NIT-{uuid.uuid4().hex[:8]}"
    datos["plan"]["nombre"] = f"Demo Restaurante {slug}"
    datos["admin"]["email"] = f"admin@{slug}.test"
    datos["identidades"] = []
    datos["canal"]["whatsapp"]["phone_number_id"] = uuid.uuid4().hex[:15]
    manifiesto = tmp_path / "resto.yaml"
    manifiesto.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    control_cache.invalidate(slug)

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        empresa_id = provision_from_manifest(str(manifiesto))
        # Idempotencia E2E: re-correr el comando entero devuelve la misma empresa.
        assert provision_from_manifest(str(manifiesto)) == empresa_id

        # --- Control DB: el plan trae los flags nuevos del pack restaurante ---
        with psycopg.connect(to_libpq(control_url), row_factory=dict_row) as cc:
            plan_id = cc.execute(
                "SELECT plan_id FROM empresas WHERE slug = %s", (slug,)
            ).fetchone()["plan_id"]
            features = set(
                cc.execute("SELECT limites FROM planes WHERE id = %s", (plan_id,))
                .fetchone()["limites"]["features"]
            )
            assert {"pack_mesas", "kds", "menu_qr", "recetas", "pack_pedidos", "pos"} <= features

        # --- Tenant DB: smoke del pack restaurante ---
        tenant_db_url = tenant_url(get_settings().tenants_direct_url_base, _db_name(slug))
        with psycopg.connect(to_libpq(tenant_db_url), row_factory=dict_row) as tc:
            def n(sql: str, *args) -> int:
                return tc.execute(sql, args).fetchone()["n"]

            conteos = {
                "productos": n("SELECT count(*) AS n FROM productos"),
                "grupos": n("SELECT count(*) AS n FROM modificador_grupos"),
                "opciones": n("SELECT count(*) AS n FROM modificador_opciones"),
                "recetas": n("SELECT count(*) AS n FROM recetas"),
                "mesas": n("SELECT count(*) AS n FROM mesas"),
                "zonas_kds": n("SELECT count(*) AS n FROM comanda_zonas"),
            }
            assert conteos["productos"] >= 27
            assert conteos["grupos"] == 3          # Proteína + Acompañantes + Personalización
            assert conteos["opciones"] == 12
            assert conteos["recetas"] == 2
            assert conteos["mesas"] == 5
            assert conteos["zonas_kds"] == 2       # parrilla + bar

            plato = tc.execute(
                "SELECT iva, tipo_impuesto FROM productos WHERE nombre = 'Plato del día'"
            ).fetchone()
            assert plato["iva"] == 8 and plato["tipo_impuesto"] == "inc"

            boca = tc.execute(
                "SELECT tarifa, recargo_por_item FROM zonas_domicilio WHERE nombre = 'Bocagrande'"
            ).fetchone()
            assert boca["tarifa"] == 4000 and boca["recargo_por_item"] == 1000

            # El stock de los insumos entró CON movimiento ENTRADA (regla #7).
            assert n(
                "SELECT count(*) AS n FROM movimientos_inventario m "
                "JOIN productos p ON p.id = m.producto_id "
                "WHERE m.tipo = 'ENTRADA' AND p.categoria = 'Insumos'"
            ) == 2

            # Idempotencia: la segunda corrida NO duplicó nada.
            assert conteos["grupos"] == n("SELECT count(*) AS n FROM modificador_grupos")
            assert conteos["recetas"] == n("SELECT count(*) AS n FROM recetas")
            assert conteos["mesas"] == n("SELECT count(*) AS n FROM mesas")
    finally:
        drop_database(_db_name(slug))
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate(slug)
        get_settings.cache_clear()
        drop_database(control_name)
