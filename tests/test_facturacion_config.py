"""Loader de config MATIAS por empresa (`modules.facturacion.config`) contra el control DB efímero.

Patrón de `test_llm_stores`: siembra empresa + `secretos_empresa` (cifrados) + `config_empresa`, y
verifica que `cargar_config_matias` descifra credenciales y arma `MatiasCredenciales`/`ConfigFiscal`.
"""
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from modules.facturacion.config import cargar_config_matias
from core.config import get_settings
from core.crypto import encrypt_split
from core.db.urls import tenant_url, to_async
from tests.conftest import create_database, drop_database


async def test_cargar_config_matias(monkeypatch):
    name = f"test_control_worker_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        master = get_settings().secrets_master_key
        em_ct, em_n = encrypt_split("bot@empresa.co", master)
        pw_ct, pw_n = encrypt_split("secreto", master)

        async with AsyncSession(engine) as s:
            eid = (
                await s.execute(
                    text(
                        "INSERT INTO empresas (nombre, nit, slug, estado) "
                        "VALUES ('Punto Rojo','900','pr','activa') RETURNING id"
                    )
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) VALUES "
                    "(:e,'matias_email',:e1,:n1), (:e,'matias_password',:e2,:n2)"
                ),
                {"e": eid, "e1": em_ct, "n1": em_n, "e2": pw_ct, "n2": pw_n},
            )
            await s.execute(
                text(
                    "INSERT INTO config_empresa (empresa_id, clave, valor) VALUES "
                    "(:e,'matias_base_url','https://matias.test/api'),"
                    "(:e,'matias_resolution','18760000001'),"
                    "(:e,'matias_prefix','FPR'),"
                    "(:e,'matias_notes','Punto Rojo'),"
                    "(:e,'matias_city_id','149')"
                ),
                {"e": eid},
            )
            await s.commit()

            cred, config = await cargar_config_matias(s, master, eid)
        assert cred.email == "bot@empresa.co" and cred.password == "secreto"
        assert cred.base_url == "https://matias.test/api"
        assert config.resolution_number == "18760000001"
        assert config.prefix == "FPR"
        assert config.notes == "Punto Rojo"
        assert config.city_id_default == "149"
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
