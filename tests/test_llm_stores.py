"""Integración: los stores leen del control DB real (config_empresa override + key cifrada).

Demuestra el camino de producción: override de proveedor/modelo por empresa y key descifrada
desde secretos_empresa, sin tocar .env. Usa un control DB efímero.
"""
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.crypto import encrypt_split
from core.db.urls import tenant_url, to_async
from core.llm.factory import PlataformaLLM, Turno, get_llm
from core.llm.stores import ControlLLMConfigStore, ControlLLMKeyStore
from tests.conftest import create_database, drop_database


async def test_stores_control_override_y_key_cifrada(monkeypatch):
    name = f"test_control_llm_{uuid.uuid4().hex[:12]}"
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
        ct, nonce = encrypt_split("sk-empresa-real", master)

        async with AsyncSession(engine) as s:
            eid = (
                await s.execute(
                    text(
                        "INSERT INTO empresas (nombre, nit, slug, estado) "
                        "VALUES ('Punto Rojo', '900', 'pr', 'activa') RETURNING id"
                    )
                )
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO config_empresa (empresa_id, clave, valor) VALUES "
                    "(:e, 'llm_provider', 'claude'), (:e, 'llm_model_worker', 'claude-haiku-x')"
                ),
                {"e": eid},
            )
            await s.execute(
                text(
                    "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) "
                    "VALUES (:e, 'anthropic_api_key', :v, :n)"
                ),
                {"e": eid, "v": ct, "n": nonce},
            )
            await s.commit()

            plataforma = PlataformaLLM(
                provider="openai", model_worker="gpt-4o-mini",
                model_orquestador="gpt-4o", keys={"openai": "sk-plataforma"},
            )
            res = await get_llm(
                eid, turno=Turno.WORKER,
                config_store=ControlLLMConfigStore(s),
                key_store=ControlLLMKeyStore(s, master),
                plataforma=plataforma,
            )
            assert res.provider_nombre == "claude"          # override de empresa
            assert res.model == "claude-haiku-x"            # override de empresa
            assert res.provider.api_key == "sk-empresa-real"  # key descifrada del control DB
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
