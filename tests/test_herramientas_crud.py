"""CRUD de herramientas por capa service/repo contra base efímera real (patrón test_maquinaria_crud).

Ejercita `HerramientasService` sobre `SqlHerramientasRepository` en un tenant efímero: alta, código
duplicado (409), filtro por estado, edición PARCIAL (PATCH), soft delete (`eliminado_en`) y los 404. El
aislamiento entre empresas se prueba en `test_aislamiento_herramientas.py` (invariante crítico).
"""
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from modules.herramientas.errors import (
    CodigoHerramientaDuplicado,
    HerramientaInexistente,
)
from modules.herramientas.repository import SqlHerramientasRepository
from modules.herramientas.schemas import HerramientaActualizar, HerramientaCrear
from modules.herramientas.service import HerramientasService


def _service(session: AsyncSession) -> HerramientasService:
    return HerramientasService(SqlHerramientasRepository(session))


def _payload(**over) -> HerramientaCrear:
    base = {"codigo": "H-001", "nombre": "Pulidora Bosch"}
    base.update(over)
    return HerramientaCrear(**base)


async def test_crear_y_obtener(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        creada = await svc.crear(_payload(cantidad=3, valor_reposicion=Decimal("250000")))
        assert creada.id is not None
        assert creada.estado == "DISPONIBLE"          # default de la spec
        assert creada.cantidad == 3
        assert creada.eliminado_en is None

        obtenida = await svc.obtener(creada.id)
        assert obtenida.id == creada.id
        assert obtenida.valor_reposicion == Decimal("250000")


async def test_crear_codigo_duplicado_409(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        await svc.crear(_payload(codigo="H-777"))
        with pytest.raises(CodigoHerramientaDuplicado):
            await svc.crear(_payload(codigo="H-777", nombre="Otra"))


async def test_listar_filtra_por_estado(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        await svc.crear(_payload(codigo="H-001", estado="DISPONIBLE"))
        await svc.crear(_payload(codigo="H-002", estado="EN_OBRA"))
        await svc.crear(_payload(codigo="H-003", estado="EN_OBRA"))

        todas = await svc.listar()
        assert {h.codigo for h in todas} == {"H-001", "H-002", "H-003"}

        en_obra = await svc.listar(estado="EN_OBRA")
        assert {h.codigo for h in en_obra} == {"H-002", "H-003"}


async def test_actualizar_parcial_solo_toca_lo_enviado(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        creada = await svc.crear(_payload(estado="DISPONIBLE", cantidad=5, ubicacion_actual="Bodega"))

        actualizada = await svc.actualizar(
            creada.id, HerramientaActualizar(estado="EN_OBRA", ubicacion_actual="Obra Norte")
        )
        assert actualizada.estado == "EN_OBRA"
        assert actualizada.ubicacion_actual == "Obra Norte"
        assert actualizada.cantidad == 5                 # no enviado → intacto


async def test_actualizar_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(HerramientaInexistente):
            await _service(s).actualizar(999999, HerramientaActualizar(nombre="X"))


async def test_soft_delete_saca_de_lista_y_404_al_obtener(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = _service(s)
        creada = await svc.crear(_payload(codigo="H-001"))

        await svc.eliminar(creada.id)

        assert await svc.listar() == []
        with pytest.raises(HerramientaInexistente):
            await svc.obtener(creada.id)

        # El código sigue ocupado (UNIQUE incluye soft-deleted) → re-alta = 409.
        with pytest.raises(CodigoHerramientaDuplicado):
            await svc.crear(_payload(codigo="H-001"))


async def test_soft_delete_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        with pytest.raises(HerramientaInexistente):
            await _service(s).eliminar(999999)
