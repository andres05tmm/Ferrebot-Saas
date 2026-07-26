"""Migración 0066: `clientes.tipo_persona` (natural/jurídica) — up/down/up limpio.

Base efímera PG (Docker 5433). Verifica el contrato: la columna existe tras `head`, el `downgrade` a
0065 la retira y re-aplicar `head` la deja igual (idempotente para el fixture). La columna es NULL
para el histórico: los clientes ya migrados no cambian de comportamiento (el dashboard deriva el tipo
del documento cuando no hay dato).
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_COL = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_name='clientes' AND column_name='tipo_persona'"
)


async def test_0066_up_down_up_limpio(tenant):
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL))).scalar_one() == 1
        # Aditiva y nullable: un alta sin el campo sigue funcionando y queda en NULL.
        await s.execute(text(
            "INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Sin tipo', 0)"
        ))
        await s.commit()
        assert (await s.execute(text(
            "SELECT tipo_persona FROM clientes WHERE nombre='Sin tipo'"
        ))).scalar_one() is None

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0065_perfil_usuario")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL))).scalar_one() == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL))).scalar_one() == 1
