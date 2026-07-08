"""Migración 0050 (Fase 7 DIAN): up/down/up limpio de la trazabilidad obra→documento fiscal y de la
máquina de estados de transmisión de nómina electrónica (CUNE). Base efímera PG (Docker 5433).

Verifica el contrato de la migración sin tocar MATIAS: presencia de columnas/enum/índice tras `head`,
que el `downgrade` a 0049 los retira (columnas vacías en dev; ver salvedad "histórico fiscal no se borra"
del docstring de la migración), y que re-aplicar `head` queda limpio (idempotente para el fixture).
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

# --- facturas_electronicas.obra_id (rastro obra→documento) ---
_COL_OBRA = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_name='facturas_electronicas' AND column_name='obra_id'"
)
_IDX_OBRA = "SELECT count(*) FROM pg_indexes WHERE indexname='ix_facturas_electronicas_obra_id'"

# --- máquina de estados de transmisión de nómina ---
_ENUM_TRANSMISION = (
    "SELECT count(*) FROM pg_type WHERE typtype='e' AND typname='estado_transmision_nomina'"
)
_COLS_DETALLE = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_name='detalles_liquidacion' "
    "AND column_name IN ('estado_transmision','intentos_transmision','transmision_respuesta')"
)


async def test_0050_up_down_up_limpio(tenant):
    # head incluye 0050: rastro obra→documento + máquina de estados de transmisión presentes.
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_OBRA))).scalar_one() == 1
        assert (await s.execute(text(_IDX_OBRA))).scalar_one() == 1
        assert (await s.execute(text(_ENUM_TRANSMISION))).scalar_one() == 1
        assert (await s.execute(text(_COLS_DETALLE))).scalar_one() == 3
        # El default backfillea las filas ya liquidadas (Ola A) a PENDIENTE: NOT NULL con server_default.
        estado_col = (
            await s.execute(
                text(
                    "SELECT is_nullable, column_default FROM information_schema.columns "
                    "WHERE table_name='detalles_liquidacion' AND column_name='estado_transmision'"
                )
            )
        ).one()
        assert estado_col.is_nullable == "NO"
        assert "PENDIENTE" in (estado_col.column_default or "")

    # downgrade a 0049: se retira TODO lo de 0050 (columnas vacías en dev; el enum se dropea).
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0049_cartera_alquiler_consumo")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_OBRA))).scalar_one() == 0
        assert (await s.execute(text(_IDX_OBRA))).scalar_one() == 0
        assert (await s.execute(text(_ENUM_TRANSMISION))).scalar_one() == 0
        assert (await s.execute(text(_COLS_DETALLE))).scalar_one() == 0

    # upgrade de vuelta a head: reaplica 0050 limpio (up/down/up sin residuos).
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_OBRA))).scalar_one() == 1
        assert (await s.execute(text(_ENUM_TRANSMISION))).scalar_one() == 1
        assert (await s.execute(text(_COLS_DETALLE))).scalar_one() == 3
