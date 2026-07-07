"""Migración tenant 0043 — modelos base del vertical construcción (upgrade/downgrade limpios).

Corre contra una base efímera real (fixture `tenant`, ya en head). Verifica (plan PIM §8, grupo 1 de §3):
  - head trae las 4 tablas nuevas y sus 3 tipos enum;
  - los literales del enum son EXACTOS a la spec: un valor válido entra, uno inexistente falla;
  - la FK maquinas.operador_asignado_id → trabajadores.id se respeta;
  - downgrade a 0042 dropea tablas Y tipos limpio; upgrade vuelve a head sin romper.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tools._alembic import downgrade_tenant, upgrade_tenant

_TABLAS = ("parametros_legales", "trabajadores", "maquinas", "herramientas")
_ENUMS = ("tipo_vinculacion", "estado_maquina", "estado_herramienta")

_EXISTE_TABLA = "SELECT to_regclass('public.' || :t) IS NOT NULL"
_CUENTA_ENUMS = "SELECT count(*) FROM pg_type WHERE typtype='e' AND typname = ANY(:nombres)"

_INSERT_TRABAJADOR = (
    "INSERT INTO trabajadores (tipo_vinculacion, documento, nombres, apellidos, cargo) "
    "VALUES (:tv, :doc, 'Juan', 'Pérez', 'Operador') RETURNING id"
)
_INSERT_MAQUINA = (
    "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default, operador_asignado_id) "
    "VALUES ('M-001', 'Vibrocompactador', 'vibrocompactador', 150000, :op)"
)


async def test_0043_tablas_enums_y_fk(tenant):
    # head (incluye 0043): las 4 tablas y los 3 enums existen.
    async with AsyncSession(tenant.engine) as s:
        for t in _TABLAS:
            assert (await s.execute(text(_EXISTE_TABLA), {"t": t})).scalar_one() is True
        assert (await s.execute(text(_CUENTA_ENUMS), {"nombres": list(_ENUMS)})).scalar_one() == 3

        # Literal válido de la spec entra; un DIRECTO opera la máquina (FK OK).
        op_id = (
            await s.execute(text(_INSERT_TRABAJADOR), {"tv": "DIRECTO", "doc": "123"})
        ).scalar_one()
        await s.execute(text(_INSERT_MAQUINA), {"op": op_id})
        await s.commit()

    # Un literal fuera del enum es rechazado (los literales son EXACTOS a la spec).
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(DBAPIError):
            await s.execute(text(_INSERT_TRABAJADOR), {"tv": "CONTRATISTA", "doc": "999"})
            await s.commit()

    # Un operador inexistente viola la FK.
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(IntegrityError):
            await s.execute(
                text("INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default, "
                     "operador_asignado_id) VALUES ('M-002', 'Retro', 'retro', 100000, 999999)")
            )
            await s.commit()

    # downgrade a 0042 → tablas y tipos se van limpio.
    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0042_cufe_recibidas_unico")
    async with AsyncSession(tenant.engine) as s:
        for t in _TABLAS:
            assert (await s.execute(text(_EXISTE_TABLA), {"t": t})).scalar_one() is False
        assert (await s.execute(text(_CUENTA_ENUMS), {"nombres": list(_ENUMS)})).scalar_one() == 0

    # upgrade vuelve a head sin romper.
    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        for t in _TABLAS:
            assert (await s.execute(text(_EXISTE_TABLA), {"t": t})).scalar_one() is True
