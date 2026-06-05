"""Migración tenant 0004 — UNIQUE(tipo, clave) en `memoria_entidades` (upgrade/downgrade limpios).

Corre contra una base efímera real (fixture `tenant`, ya en head). Verifica:
  - head trae la restricción UNIQUE(tipo, clave) y permite `ON CONFLICT (tipo, clave)` (upsert);
  - downgrade a 0003 la revierte limpio; upgrade vuelve a head sin romper.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_CONSTRAINT = "uq_memoria_entidades_tipo_clave"
_EXISTE = "SELECT count(*) FROM pg_constraint WHERE conname = :c"


async def test_0004_unique_tipo_clave_y_upsert(tenant):
    # head (incluye 0004): la restricción UNIQUE existe.
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_EXISTE), {"c": _CONSTRAINT})).scalar_one() == 1

        # ON CONFLICT (tipo, clave) DO UPDATE → upsert idempotente del scratch del chat.
        for valor in ('{"id": 1, "nombre": "Ana"}', '{"id": 2, "nombre": "Beto"}'):
            await s.execute(
                text(
                    "INSERT INTO memoria_entidades (tipo, clave, valor) "
                    "VALUES ('ultimo_cliente', '555', CAST(:v AS jsonb)) "
                    "ON CONFLICT (tipo, clave) DO UPDATE SET valor = EXCLUDED.valor"
                ),
                {"v": valor},
            )
        await s.commit()

        filas = (
            await s.execute(
                text("SELECT count(*) FROM memoria_entidades WHERE tipo='ultimo_cliente' AND clave='555'")
            )
        ).scalar_one()
        nombre = (
            await s.execute(
                text("SELECT valor->>'nombre' FROM memoria_entidades WHERE tipo='ultimo_cliente' AND clave='555'")
            )
        ).scalar_one()
    assert filas == 1            # no duplicó: el upsert actualizó la misma fila
    assert nombre == "Beto"      # último escritor gana

    # downgrade a 0003 → la restricción se va limpio.
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0003_dinero_idem")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_EXISTE), {"c": _CONSTRAINT})).scalar_one() == 0

    # upgrade vuelve a head sin romper.
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_EXISTE), {"c": _CONSTRAINT})).scalar_one() == 1
