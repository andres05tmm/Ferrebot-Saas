"""Servicio de trabajadores: validación de dominio sobre el repositorio (sin SQL).

Reglas: `documento` único (409 si ya existe, incluida una baja lógica previa — la constraint UNIQUE de
la base lo abarca); operar sobre un id inexistente o ya dado de baja → 404. El servicio depende del
puerto `TrabajadoresRepo` (lo implementa `SqlTrabajadoresRepository`; los tests lo falsean).
"""
from datetime import date
from typing import Protocol

from core.config.timezone import today_co
from modules.trabajadores.errors import (
    AsignacionInexistente,
    AsignacionSolapada,
    ObraNoAsignable,
    TrabajadorDuplicado,
    TrabajadorInexistente,
)
from modules.trabajadores.models import AsignacionTrabajadorObra, Trabajador
from modules.trabajadores.schemas import (
    AsignacionTrabajadorActualizar,
    AsignacionTrabajadorCrear,
    TrabajadorActualizar,
    TrabajadorCrear,
)


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
    async def listar_asignaciones(self, trabajador_id: int) -> list[AsignacionTrabajadorObra]: ...
    async def obtener_asignacion(
        self, trabajador_id: int, asignacion_id: int
    ) -> AsignacionTrabajadorObra | None: ...
    async def asignacion_solapada(
        self,
        trabajador_id: int,
        fecha_inicio: date,
        fecha_fin: date | None,
        *,
        excluir_id: int | None = None,
    ) -> bool: ...
    async def obra_asignable(self, obra_id: int) -> str | None: ...
    async def crear_asignacion(
        self,
        *,
        trabajador_id: int,
        obra_id: int,
        fecha_inicio: date,
        fecha_fin: date | None,
        activa: bool = True,
    ) -> AsignacionTrabajadorObra: ...
    async def actualizar_asignacion(
        self, asig: AsignacionTrabajadorObra, cambios: dict
    ) -> AsignacionTrabajadorObra: ...


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

    # ---- CRUD de asignaciones trabajador→obra (Calendario de obra) -------------------------------
    async def listar_asignaciones(self, trabajador_id: int) -> list[AsignacionTrabajadorObra]:
        """Asignaciones a obra del trabajador. 404 si el trabajador no existe."""
        await self.obtener(trabajador_id)   # valida existencia (404 si no)
        return await self._repo.listar_asignaciones(trabajador_id)

    async def crear_asignacion(
        self, trabajador_id: int, datos: AsignacionTrabajadorCrear
    ) -> AsignacionTrabajadorObra:
        """Asigna el trabajador a una obra (Calendario de obra). Sin dinero ni transición de estado.

        Validaciones: trabajador vivo (404 TrabajadorInexistente); obra existente y no LIQUIDADA
        (ObraNoAsignable `inexistente`/`liquidada`); sin solape con otra asignación activa (AsignacionSolapada,
        un trabajador no está en dos obras el mismo día). `fecha_inicio` default hoy Colombia (regla #4)."""
        await self.obtener(trabajador_id)   # valida existencia (404 si no)

        estado_obra = await self._repo.obra_asignable(datos.obra_id)
        if estado_obra is None:
            raise ObraNoAsignable(datos.obra_id, "inexistente")
        if estado_obra == "LIQUIDADA":
            raise ObraNoAsignable(datos.obra_id, "liquidada")

        inicio = datos.fecha_inicio or today_co()
        if await self._repo.asignacion_solapada(trabajador_id, inicio, datos.fecha_fin):
            raise AsignacionSolapada(trabajador_id, inicio, datos.fecha_fin)

        return await self._repo.crear_asignacion(
            trabajador_id=trabajador_id,
            obra_id=datos.obra_id,
            fecha_inicio=inicio,
            fecha_fin=datos.fecha_fin,
        )

    async def actualizar_asignacion(
        self, trabajador_id: int, asignacion_id: int, datos: AsignacionTrabajadorActualizar
    ) -> AsignacionTrabajadorObra:
        """Edición parcial de una asignación. 404 si no existe para ese trabajador; revalida el solape si
        cambia `fecha_fin` (y sigue activa). Sin transición de estado (el trabajador no lleva estado)."""
        asig = await self._repo.obtener_asignacion(trabajador_id, asignacion_id)
        if asig is None:
            raise AsignacionInexistente(asignacion_id)

        cambios = datos.model_dump(exclude_unset=True)
        nueva_fin = cambios.get("fecha_fin", asig.fecha_fin)
        nueva_activa = cambios.get("activa", asig.activa)
        # Revalida el solape si cambia el rango O si se REACTIVA una asignación cerrada: mientras estuvo
        # inactiva pudo crearse otra activa sobre el mismo rango.
        reactivada = nueva_activa and not asig.activa
        if (
            nueva_activa
            and ("fecha_fin" in cambios or reactivada)
            and await self._repo.asignacion_solapada(
                trabajador_id, asig.fecha_inicio, nueva_fin, excluir_id=asignacion_id
            )
        ):
            raise AsignacionSolapada(trabajador_id, asig.fecha_inicio, nueva_fin)

        return await self._repo.actualizar_asignacion(asig, cambios)
