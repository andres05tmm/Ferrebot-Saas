"""Provisioning completo de una empresa (tools.provision_tenant). Requiere Postgres.

VALORES FALSOS (nunca secretos reales, nunca red): provisiona desde un dict de prueba contra un
control DB efímero + una app DB efímera, y verifica que el lector real (`cargar_config_matias` /
`ControlSecretosBot` / `leer_branding`) recupera lo cargado, que el admin queda con su telegram_id,
y que re-ejecutar es idempotente (no duplica).
"""
import uuid

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

import core.db.session as session_mod
from apps.bot.repos import ControlSecretosBot
from core.config import get_settings
from core.db.urls import tenant_url, to_libpq
from core.tenancy.cache import control_cache
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.control_repo import leer_branding
from modules.facturacion.config import cargar_config_matias
from tests.conftest import create_database, drop_database
from tools.provision_tenant import _db_name, cargar_plan_features, provision_tenant_full


def _datos(slug: str) -> dict:
    return {
        "slug": slug, "nombre": "Prueba SAS", "nit": f"NIT-{slug}",
        "admin": {"nombre": "Admin Prueba", "telegram_id": 555111222},
        "secretos": {
            "telegram_token": "111222:FAKE-bot-token",
            "matias_email": "fake@empresa.test", "matias_password": "fake-pw",
        },
        "config": {
            "matias_base_url": "http://matias.fake", "matias_resolution": "18760000999",
            "matias_prefix": "FPX", "matias_notes": "Prueba", "matias_city_id": "149",
        },
        "branding": {
            "color_primario": "#0d6efd", "logo_url": "http://x/logo.png",
            "nombre_comercial": "Prueba", "dominio": "prueba.test",
        },
        "plan": {"nombre": "Pro", "features": ["facturacion_electronica", "fiados"]},
        "features_override": {"fiados": False},   # quita 'fiados' del plan para esta empresa
    }


async def test_provision_full_carga_secretos_config_branding_admin(monkeypatch):
    # Control DB efímero (patrón de test_e2e_*).
    control_name = f"test_control_prov_{uuid.uuid4().hex[:12]}"
    control_url = tenant_url(get_settings().tenants_direct_url_base, control_name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", control_url)
    get_settings.cache_clear()
    monkeypatch.setattr(session_mod, "_control_sessionmaker", None)
    monkeypatch.setattr(session_mod, "_control_engine", None)

    slug = f"tprov{uuid.uuid4().hex[:10]}"
    datos = _datos(slug)
    control_cache.invalidate(slug)
    master = get_settings().secrets_master_key

    create_database(control_name)
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")

        empresa_id = provision_tenant_full(datos)
        # Idempotente: re-ejecutar no rompe ni cambia el id.
        assert provision_tenant_full(datos) == empresa_id

        async with session_mod.control_session() as cs:
            # secretos + config, vía el lector real.
            cred, fiscal = await cargar_config_matias(cs, master, empresa_id)
            assert cred.email == "fake@empresa.test" and cred.password == "fake-pw"
            assert cred.base_url == "http://matias.fake"
            assert fiscal.prefix == "FPX" and fiscal.resolution_number == "18760000999"
            assert fiscal.city_id_default == "149"
            # bot-token descifra igual.
            assert await ControlSecretosBot(cs, master).bot_token(empresa_id) == "111222:FAKE-bot-token"
            # branding.
            brand = await leer_branding(cs, empresa_id)
            assert brand["color_primario"] == "#0d6efd"
            assert brand["nombre_comercial"] == "Prueba"
            # idempotencia: una sola fila por (empresa, clave).
            n_sec = (await cs.execute(
                text("SELECT count(*) FROM secretos_empresa WHERE empresa_id=:e"), {"e": empresa_id})).scalar_one()
            n_cfg = (await cs.execute(
                text("SELECT count(*) FROM config_empresa WHERE empresa_id=:e"), {"e": empresa_id})).scalar_one()
            n_brand = (await cs.execute(
                text("SELECT count(*) FROM branding WHERE empresa_id=:e"), {"e": empresa_id})).scalar_one()
            # plan + features: efectivas = plan ∪ overrides; el override quitó 'fiados'.
            efectivas = await ControlCapacidades(cs).efectivas(empresa_id)
            n_feat = (await cs.execute(
                text("SELECT count(*) FROM empresa_features WHERE empresa_id=:e"), {"e": empresa_id})).scalar_one()
        assert (n_sec, n_cfg, n_brand) == (3, 5, 1)
        assert "facturacion_electronica" in efectivas
        assert "fiados" not in efectivas          # el override habilitada=false lo quita
        assert n_feat == 1                          # idempotente: una fila de override

        # Admin con su telegram_id en la base del tenant.
        tenant_db_url = tenant_url(get_settings().tenants_direct_url_base, _db_name(slug))
        with psycopg.connect(to_libpq(tenant_db_url)) as conn:
            filas = conn.execute("SELECT telegram_id FROM usuarios WHERE rol='admin'").fetchall()
        assert len(filas) == 1 and filas[0][0] == 555111222
    finally:
        drop_database(_db_name(slug))
        if session_mod._control_engine is not None:
            await session_mod._control_engine.dispose()
        control_cache.invalidate(slug)
        get_settings.cache_clear()
        drop_database(control_name)


# La validación corre ANTES de cualquier escritura (no toca la BD), así que estos casos no
# necesitan Postgres: una feature inválida o una dependencia incumplida lanzan ValueError y no se
# escribe nada.

def test_feature_desconocida_falla_sin_escribir():
    with pytest.raises(ValueError, match="feature desconocida"):
        cargar_plan_features(1, {"plan": {"nombre": "X", "features": ["no_existe"]}})


def test_dependencia_incumplida_falla_sin_escribir():
    # libro_iva requiere facturacion_electronica o compras_fiscal; sin ellos → error.
    with pytest.raises(ValueError, match="dependencias"):
        cargar_plan_features(1, {"plan": {"nombre": "X", "features": ["libro_iva"]}})
