"""Aislamiento multi-tenant sobre una tabla del vertical construcción (invariante crítico, test-primero).

Calca la mecánica de `test_tenant_isolation.py` (ventas) para `maquinas` (migración 0043): cada empresa
es una base distinta, así que una máquina dada de alta en la empresa A JAMÁS aparece al consultar la B.
Es el invariante no negociable de multi-tenancy (.claude/rules/multitenancy.md) aplicado a las tablas
nuevas: la frontera la da la base, no una columna `empresa_id`.

Aún no existe la capa de repositorio/servicio de máquinas (Fase 1), así que se inserta por SQL directo
(como `test_migracion_tenant_0043`): `codigo`, `nombre`, `tipo` y `precio_hora_default` son NOT NULL.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_INSERT_MAQUINA = (
    "INSERT INTO maquinas (codigo, nombre, tipo, precio_hora_default) "
    "VALUES (:cod, :nom, :tipo, :precio)"
)


async def _dar_de_alta_maquina(engine, *, codigo: str) -> None:
    async with AsyncSession(engine) as s:
        await s.execute(
            text(_INSERT_MAQUINA),
            {"cod": codigo, "nom": "Vibrocompactador", "tipo": "vibrocompactador", "precio": 150000},
        )
        await s.commit()


async def _contar_maquinas(engine) -> int:
    async with AsyncSession(engine) as s:
        return (await s.execute(text("SELECT count(*) FROM maquinas"))).scalar_one()


async def test_empresa_A_no_ve_maquinas_de_empresa_B(tenant_factory):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    await _dar_de_alta_maquina(empresa_a.engine, codigo="M-001")   # máquina solo en A

    assert await _contar_maquinas(empresa_a.engine) == 1   # A ve su máquina
    assert await _contar_maquinas(empresa_b.engine) == 0   # B no ve nada de A
