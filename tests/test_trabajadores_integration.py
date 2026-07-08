"""Integración del repositorio/servicio de trabajadores contra Postgres efímero.

Verifica la dedup por `documento` (la columna es UNIQUE), los filtros por `tipo_vinculacion` y `activo`,
y el soft delete: tras la baja lógica el trabajador desaparece de `obtener`/`listar` pero la fila sigue
en la tabla y su documento sigue reservado (la unicidad abarca también las bajas).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.trabajadores.errors import TrabajadorDuplicado
from modules.trabajadores.repository import SqlTrabajadoresRepository
from modules.trabajadores.schemas import TrabajadorCrear
from modules.trabajadores.service import TrabajadoresService


def _directo(documento="1", activo=True):
    return TrabajadorCrear(
        tipo_vinculacion="DIRECTO", documento=documento, nombres="Ana", apellidos="Ruiz",
        cargo="Operador", activo=activo, salario_base=Decimal("1500000"),
    )


def _patacaliente(documento="2", activo=False):
    return TrabajadorCrear(
        tipo_vinculacion="PATACALIENTE", documento=documento, nombres="Beto", apellidos="Sosa",
        cargo="Ayudante", activo=activo, tarifa_hora=Decimal("15000"),
    )


async def test_dedup_filtros_y_soft_delete(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = TrabajadoresService(SqlTrabajadoresRepository(s))
        d = await svc.crear(_directo("1"))
        p = await svc.crear(_patacaliente("2"))
        await s.commit()
        did, pid = d.id, p.id

    # dedup: mismo documento → error de dominio (409 en el router)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = TrabajadoresService(SqlTrabajadoresRepository(s))
        with pytest.raises(TrabajadorDuplicado):
            await svc.crear(_directo("1"))

    # filtros por vínculo y por estado laboral
    async with AsyncSession(tenant.engine) as s:
        repo = SqlTrabajadoresRepository(s)
        assert {t.id for t in await repo.listar()} == {did, pid}
        assert [t.id for t in await repo.listar(tipo_vinculacion="PATACALIENTE")] == [pid]
        assert [t.id for t in await repo.listar(activo=False)] == [pid]
        assert [t.id for t in await repo.listar(activo=True)] == [did]

    # soft delete
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlTrabajadoresRepository(s)
        await repo.soft_delete(await repo.obtener(pid))
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        repo = SqlTrabajadoresRepository(s)
        assert await repo.obtener(pid) is None                     # oculto para el API
        assert [t.id for t in await repo.listar()] == [did]        # excluido del listado
        assert await repo.buscar_por_documento("2") is not None    # unique lo sigue abarcando
        total = (await s.execute(text("SELECT count(*) FROM trabajadores"))).scalar_one()
        assert total == 2                                          # soft, no hard: la fila sigue
