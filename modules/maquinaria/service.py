"""Servicio de maquinaria: validación de dominio sobre el repositorio (sin SQL).

Calca `modules/inventario/service.py`: el código de máquina es único (409); la edición es PARCIAL
(solo los campos enviados en el PATCH). El SQL vive en `SqlMaquinasRepository`; aquí solo la lógica.
"""
from modules.maquinaria.errors import CodigoMaquinaDuplicado, MaquinaInexistente
from modules.maquinaria.models import (
    AsignacionMaquinaObra,
    Maquina,
    RegistroHorasMaquina,
)
from modules.maquinaria.repository import SqlMaquinasRepository
from modules.maquinaria.schemas import MaquinaActualizar, MaquinaCrear


class MaquinariaService:
    def __init__(self, repo: SqlMaquinasRepository) -> None:
        self._repo = repo

    async def listar(self, *, estado: str | None = None, q: str | None = None) -> list[Maquina]:
        return await self._repo.listar(estado=estado, q=q)

    async def obtener(self, maquina_id: int) -> Maquina:
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)
        return maquina

    async def crear(self, datos: MaquinaCrear) -> Maquina:
        """Da de alta la máquina. 409 si el código ya lo usa otra (incluida una eliminada: el UNIQUE
        de la BD no distingue soft delete)."""
        if await self._repo.codigo_existe(datos.codigo):
            raise CodigoMaquinaDuplicado(datos.codigo)
        return await self._repo.crear(datos)

    async def actualizar(self, maquina_id: int, datos: MaquinaActualizar) -> Maquina:
        """Edición parcial: solo los campos presentes en el PATCH. 404 si no existe; 409 si el nuevo
        código lo usa otra máquina."""
        cambios = datos.model_dump(exclude_unset=True)
        codigo = cambios.get("codigo")
        if codigo is not None and await self._repo.codigo_existe(codigo, excluir_id=maquina_id):
            raise CodigoMaquinaDuplicado(codigo)
        maquina = await self._repo.obtener(maquina_id)
        if maquina is None:
            raise MaquinaInexistente(maquina_id)
        return await self._repo.actualizar(maquina, cambios)

    async def eliminar(self, maquina_id: int) -> None:
        """Soft delete (`eliminado_en`). 404 si no existe o ya estaba eliminada."""
        if not await self._repo.soft_delete(maquina_id):
            raise MaquinaInexistente(maquina_id)

    # ---- Lecturas de operación (solo lectura) -------------------------------
    async def listar_asignaciones(self, maquina_id: int) -> list[AsignacionMaquinaObra]:
        return await self._repo.listar_asignaciones(maquina_id)

    async def listar_horas(
        self, maquina_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[RegistroHorasMaquina]:
        return await self._repo.listar_horas(maquina_id, limite=limite, offset=offset)
