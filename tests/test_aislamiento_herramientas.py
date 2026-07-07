"""Aislamiento multi-tenant sobre `herramientas` (invariante crítico, test-primero).

Calca `test_aislamiento_maquinas.py` para herramientas: cada empresa es una base distinta, así que una
herramienta dada de alta en la empresa A JAMÁS aparece al consultar la B. Es el invariante no negociable
de multi-tenancy (.claude/rules/multitenancy.md): la frontera la da la base, no una columna `empresa_id`.

A diferencia del test de máquinas (que insertaba por SQL crudo por no existir la capa aún), aquí se pasa
por la capa real de servicio/repositorio de la Fase 1 — así el aislamiento se prueba sobre el código que
efectivamente sirve las herramientas.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from modules.herramientas.repository import SqlHerramientasRepository
from modules.herramientas.schemas import HerramientaCrear
from modules.herramientas.service import HerramientasService


async def _dar_de_alta_herramienta(engine, *, codigo: str) -> None:
    async with AsyncSession(engine) as s:
        await HerramientasService(SqlHerramientasRepository(s)).crear(
            HerramientaCrear(codigo=codigo, nombre="Pulidora")
        )
        await s.commit()


async def _contar_herramientas(engine) -> int:
    async with AsyncSession(engine) as s:
        return len(await HerramientasService(SqlHerramientasRepository(s)).listar())


async def test_empresa_A_no_ve_herramientas_de_empresa_B(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    await _dar_de_alta_herramienta(empresa_a.engine, codigo="H-001")   # herramienta solo en A

    assert await _contar_herramientas(empresa_a.engine) == 1   # A ve su herramienta
    assert await _contar_herramientas(empresa_b.engine) == 0   # B no ve nada de A
