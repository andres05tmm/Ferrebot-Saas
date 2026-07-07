"""Servicio de herramientas: validación de dominio sobre el repositorio (sin SQL).

Calca `modules/maquinaria/service.py`: código único (409), edición PARCIAL (solo los campos del PATCH),
soft delete (404 si no existe).
"""
from modules.herramientas.errors import (
    CodigoHerramientaDuplicado,
    HerramientaInexistente,
)
from modules.herramientas.models import Herramienta
from modules.herramientas.repository import SqlHerramientasRepository
from modules.herramientas.schemas import HerramientaActualizar, HerramientaCrear


class HerramientasService:
    def __init__(self, repo: SqlHerramientasRepository) -> None:
        self._repo = repo

    async def listar(self, *, estado: str | None = None, q: str | None = None) -> list[Herramienta]:
        return await self._repo.listar(estado=estado, q=q)

    async def obtener(self, herramienta_id: int) -> Herramienta:
        herramienta = await self._repo.obtener(herramienta_id)
        if herramienta is None:
            raise HerramientaInexistente(herramienta_id)
        return herramienta

    async def crear(self, datos: HerramientaCrear) -> Herramienta:
        """Da de alta la herramienta. 409 si el código ya lo usa otra (incluida una eliminada)."""
        if await self._repo.codigo_existe(datos.codigo):
            raise CodigoHerramientaDuplicado(datos.codigo)
        return await self._repo.crear(datos)

    async def actualizar(
        self, herramienta_id: int, datos: HerramientaActualizar
    ) -> Herramienta:
        """Edición parcial: solo los campos presentes en el PATCH. 404 si no existe; 409 si el nuevo
        código lo usa otra herramienta."""
        cambios = datos.model_dump(exclude_unset=True)
        codigo = cambios.get("codigo")
        if codigo is not None and await self._repo.codigo_existe(codigo, excluir_id=herramienta_id):
            raise CodigoHerramientaDuplicado(codigo)
        herramienta = await self._repo.obtener(herramienta_id)
        if herramienta is None:
            raise HerramientaInexistente(herramienta_id)
        return await self._repo.actualizar(herramienta, cambios)

    async def eliminar(self, herramienta_id: int) -> None:
        """Soft delete (`eliminado_en`). 404 si no existe o ya estaba eliminada."""
        if not await self._repo.soft_delete(herramienta_id):
            raise HerramientaInexistente(herramienta_id)
