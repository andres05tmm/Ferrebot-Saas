"""Migración tenant 0005 — drop de `config_empresa` vestigial en la app DB (upgrade/downgrade limpios).

La 0001 creó `config_empresa` en la app DB, pero la config no-secreta por empresa vive en el CONTROL
DB (control 0002, con empresa_id): los lectores consultan `WHERE empresa_id = :e`, columna que la del
tenant no tiene. Era vestigial. Corre contra una base efímera real (fixture `tenant`, ya en head).
"""
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant


async def _tabla_existe(engine, nombre: str) -> bool:
    async with engine.connect() as conn:
        return await conn.run_sync(lambda c: inspect(c).has_table(nombre))


async def test_0005_config_empresa_no_existe_en_head(tenant):
    # head (incluye 0005): la tabla vestigial ya no está en la app DB.
    assert await _tabla_existe(tenant.engine, "config_empresa") is False

    # downgrade a 0004 → se recrea limpio (clave TEXT PK, valor JSONB).
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0004_memoria_uq")
    assert await _tabla_existe(tenant.engine, "config_empresa") is True
    async with AsyncSession(tenant.engine) as s:
        await s.execute(
            text("INSERT INTO config_empresa (clave, valor) VALUES ('k', CAST('true' AS jsonb))")
        )
        await s.commit()
        valor = (await s.execute(text("SELECT valor FROM config_empresa WHERE clave='k'"))).scalar_one()
    assert valor is True

    # upgrade vuelve a head → la tabla se va de nuevo, sin romper.
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    assert await _tabla_existe(tenant.engine, "config_empresa") is False
