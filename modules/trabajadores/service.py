"""Servicio de trabajadores: validación de dominio sobre el repositorio (sin SQL).

Reglas: `documento` único (409 si ya existe, incluida una baja lógica previa — la constraint UNIQUE de
la base lo abarca); operar sobre un id inexistente o ya dado de baja → 404. El servicio depende del
puerto `TrabajadoresRepo` (lo implementa `SqlTrabajadoresRepository`; los tests lo falsean).
"""
from typing import Protocol

from modules.trabajadores.errors import TrabajadorDuplicado, TrabajadorInexistente
from modules.trabajadores.models import Trabajador
from modules.trabajadores.schemas import TrabajadorActualizar, TrabajadorCrear


class TrabajadoresRepo(Protocol):
    """Puerto de datos de trabajadores (lo implementa SqlTrabajadoresRepository; los tests lo falsean)."""

    async def buscar_por_documento(self, documento: str) -> Trabajador | None: ...
    async def obtener(self, trabajador_id: int) -> Trabajador | None: ...
    async def listar(
        self, *, tipo_vinculacion: str | None = None, activo: bool | None = None
    ) -> list[Trabajador]: ...
    async def crear(self, datos: TrabajadorCrear) -> Trabajador: ...
    async def actualizar(self, trabajador: Trabajador, cambios: dict) -> Trabajador: ...
    async def soft_delete(self, trabajador: Trabajador) -> None: ...


class TrabajadoresService:
    def __init__(self, repo: TrabajadoresRepo) -> None:
        self._repo = repo

    async def crear(self, datos: TrabajadorCrear) -> Trabajador:
        """Da de alta el trabajador; si el documento ya existe → TrabajadorDuplicado (409)."""
        if await self._repo.buscar_por_documento(datos.documento) is not None:
            raise TrabajadorDuplicado(datos.documento)
        return await self._repo.crear(datos)

    async def obtener(self, trabajador_id: int) -> Trabajador:
        trabajador = await self._repo.obtener(trabajador_id)
        if trabajador is None:
            raise TrabajadorInexistente(trabajador_id)
        return trabajador

    async def listar(
        self, *, tipo_vinculacion: str | None = None, activo: bool | None = None
    ) -> list[Trabajador]:
        return await self._repo.listar(tipo_vinculacion=tipo_vinculacion, activo=activo)

    async def actualizar(
        self, trabajador_id: int, datos: TrabajadorActualizar
    ) -> Trabajador:
        """Parche parcial. 404 si no existe; 409 si el nuevo documento choca con otro trabajador."""
        trabajador = await self.obtener(trabajador_id)
        cambios = datos.model_dump(exclude_unset=True)
        nuevo_doc = cambios.get("documento")
        if nuevo_doc is not None and nuevo_doc != trabajador.documento:
            otro = await self._repo.buscar_por_documento(nuevo_doc)
            if otro is not None and otro.id != trabajador.id:
                raise TrabajadorDuplicado(nuevo_doc)
        return await self._repo.actualizar(trabajador, cambios)

    async def eliminar(self, trabajador_id: int) -> None:
        """Baja lógica (soft delete). 404 si no existe o ya estaba dado de baja."""
        trabajador = await self.obtener(trabajador_id)
        await self._repo.soft_delete(trabajador)
