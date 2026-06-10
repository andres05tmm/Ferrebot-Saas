"""F2.1.2 — registro del webhook MATIAS en el control DB efímero (valida la migración 0007).

Patrón de `test_facturacion_config`: control DB a head, siembra una empresa y verifica el roundtrip
`guardar_registro_webhook` → `buscar_empresa_por_token` → `leer_secret_webhook` (secret CIFRADO)."""
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import get_settings
from core.db.urls import tenant_url, to_async
from modules.facturacion.webhook_repo import (
    buscar_empresa_por_token,
    guardar_registro_webhook,
    leer_secret_webhook,
)
from tests.conftest import create_database, drop_database


async def test_registro_webhook_roundtrip(monkeypatch):
    name = f"test_control_wh_{uuid.uuid4().hex[:12]}"
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
        async with AsyncSession(engine) as s:
            eid = (
                await s.execute(
                    text(
                        "INSERT INTO empresas (nombre, nit, slug, estado) "
                        "VALUES ('Punto Rojo','900','pr','activa') RETURNING id"
                    )
                )
            ).scalar_one()
            await guardar_registro_webhook(
                s, master, eid, token="tok-abc", callback_url="https://app/webhooks/matias/tok-abc",
                secret="wh-secret",
            )
            await s.commit()

            assert await buscar_empresa_por_token(s, "tok-abc") == eid
            assert await buscar_empresa_por_token(s, "desconocido") is None
            assert await leer_secret_webhook(s, master, eid) == "wh-secret"   # descifra el secret

            # Re-registro (idempotente por empresa): reemplaza token y secret.
            await guardar_registro_webhook(
                s, master, eid, token="tok-xyz", callback_url="https://app/webhooks/matias/tok-xyz",
                secret="wh-secret-2",
            )
            await s.commit()
            assert await buscar_empresa_por_token(s, "tok-abc") is None       # token viejo ya no resuelve
            assert await buscar_empresa_por_token(s, "tok-xyz") == eid
            assert await leer_secret_webhook(s, master, eid) == "wh-secret-2"
    finally:
        await engine.dispose()
        get_settings.cache_clear()
        drop_database(name)
