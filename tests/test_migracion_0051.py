"""Migración 0051 (polish Stream C): up/down/up limpio del dedup de colitas (`obras.ultimo_aviso_colita_en`)
y de la clave natural única de la asistencia diaria (`uq_registros_asistencia_trabajador_fecha`). Base
efímera PG (Docker 5433).

Verifica el contrato de la migración: presencia de la columna y del índice único tras `head`, que el
`downgrade` a 0050 los retira, y que re-aplicar `head` queda limpio (idempotente para el fixture). Además
comprueba que el índice único REALMENTE veta un segundo registro del mismo (trabajador, fecha).
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_COL_COLITA = (
    "SELECT count(*) FROM information_schema.columns "
    "WHERE table_name='obras' AND column_name='ultimo_aviso_colita_en'"
)
_IDX_ASISTENCIA = (
    "SELECT count(*) FROM pg_indexes "
    "WHERE indexname='uq_registros_asistencia_trabajador_fecha'"
)


async def test_0051_up_down_up_limpio(tenant):
    # head incluye 0051: la columna de dedup y el índice único de asistencia existen.
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_COLITA))).scalar_one() == 1
        assert (await s.execute(text(_IDX_ASISTENCIA))).scalar_one() == 1

    # downgrade a 0050: se retira TODO lo de 0051 (columna vacía en dev; el índice se dropea).
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0050_fe_obra_nomina_cune")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_COLITA))).scalar_one() == 0
        assert (await s.execute(text(_IDX_ASISTENCIA))).scalar_one() == 0

    # upgrade de vuelta a head: reaplica 0051 limpio (up/down/up sin residuos).
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_COL_COLITA))).scalar_one() == 1
        assert (await s.execute(text(_IDX_ASISTENCIA))).scalar_one() == 1


async def test_0051_asistencia_unica_veta_duplicado(tenant):
    """El índice único (trabajador_id, fecha) veta un segundo registro del mismo día a nivel de base."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        tid = (
            await s.execute(
                text(
                    "INSERT INTO trabajadores (tipo_vinculacion, documento, nombres, apellidos, cargo) "
                    "VALUES ('DIRECTO', 'x1', 'Ana', 'Ruiz', 'Operador') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO registros_asistencia (trabajador_id, fecha, horas_trabajadas) "
                "VALUES (:t, '2026-07-01', 8)"
            ),
            {"t": tid},
        )
        await s.commit()
        with pytest.raises(IntegrityError):
            await s.execute(
                text(
                    "INSERT INTO registros_asistencia (trabajador_id, fecha, horas_trabajadas) "
                    "VALUES (:t, '2026-07-01', 4)"
                ),
                {"t": tid},
            )
        await s.rollback()
