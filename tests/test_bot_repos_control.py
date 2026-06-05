"""Backfill de cobertura: `ControlSecretosBot` y `ControlCapacidades` sobre un control DB efímero.

El código ya existe (apps/bot/repos.py); estas pruebas fijan su contrato real:
  - los secretos del bot (webhook secret, bot token) se descifran al valor original; None si no existe;
  - las features efectivas = features del plan ∪ overrides habilitados − overrides deshabilitados.

Patrón de control DB efímero tomado de tests/test_llm_stores.py (alembic upgrade head + NullPool).
"""
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from apps.bot.repos import ControlCapacidades, ControlSecretosBot
from core.config import get_settings
from core.crypto import encrypt_split
from core.db.urls import tenant_url, to_async
from tests.conftest import create_database, drop_database


@pytest.fixture
async def control_engine(monkeypatch):
    """Control DB efímero, migrado a head; lo destruye al final."""
    name = f"test_control_bot_{uuid.uuid4().hex[:12]}"
    url = tenant_url(get_settings().tenants_direct_url_base, name)
    monkeypatch.setenv("CONTROL_DATABASE_URL", url)
    get_settings.cache_clear()
    create_database(name)
    engine = create_async_engine(
        to_async(url), poolclass=NullPool, connect_args={"statement_cache_size": 0}
    )
    try:
        command.upgrade(Config("migrations/control/alembic.ini"), "head")
        yield engine
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)


async def test_secretos_bot_roundtrip(control_engine):
    master = get_settings().secrets_master_key
    ws_ct, ws_n = encrypt_split("secreto-webhook", master)
    tok_ct, tok_n = encrypt_split("123456:ABC-bot-token", master)

    async with AsyncSession(control_engine) as s:
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
                "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) VALUES "
                "(:e, 'telegram_webhook_secret', :c1, :n1), (:e, 'telegram_token', :c2, :n2)"
            ),
            {"e": eid, "c1": ws_ct, "n1": ws_n, "c2": tok_ct, "n2": tok_n},
        )
        await s.commit()

        secretos = ControlSecretosBot(s, master)
        assert await secretos.webhook_secret(eid) == "secreto-webhook"   # descifra al original
        assert await secretos.bot_token(eid) == "123456:ABC-bot-token"
        assert await secretos.webhook_secret(999) is None                # empresa/clave inexistente
        assert await secretos.bot_token(999) is None


async def test_capacidades_efectivas(control_engine):
    async with AsyncSession(control_engine) as s:
        pid = (
            await s.execute(
                text(
                    "INSERT INTO planes (nombre, limites) "
                    "VALUES ('Pro', CAST(:lim AS JSONB)) RETURNING id"
                ),
                {"lim": '{"features": ["ventas", "fiados", "bot_telegram"]}'},
            )
        ).scalar_one()
        eid = (
            await s.execute(
                text(
                    "INSERT INTO empresas (nombre, nit, slug, estado, plan_id) "
                    "VALUES ('Punto Rojo', '900', 'pr', 'activa', :p) RETURNING id"
                ),
                {"p": pid},
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO empresa_features (empresa_id, feature, habilitada) VALUES "
                "(:e, 'ventas_voz', true), (:e, 'fiados', false)"
            ),
            {"e": eid},
        )
        await s.commit()

        efectivas = await ControlCapacidades(s).efectivas(eid)
        # plan = {ventas, fiados, bot_telegram}; override +ventas_voz, −fiados.
        assert efectivas == frozenset({"ventas", "bot_telegram", "ventas_voz"})
