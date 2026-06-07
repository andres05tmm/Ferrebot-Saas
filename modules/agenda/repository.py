"""Repositorio del pack Agenda/Citas: único lugar con SQL del módulo (regla no negociable #2).

Esta entrega es **solo la capa de datos**: se fija el *puerto* (`AgendaRepo`) y el esqueleto de su
implementación SQL. El cuerpo de cada método llega en el prompt del motor de disponibilidad —aquí los
stubs levantan `NotImplementedError` a propósito. NO hay lógica de motor (cálculo de cupos, locks,
políticas de cancelación) en este archivo todavía.

El motor consumirá este puerto; los tests lo falsean. Toda acción sobre `citas` queda acotada al
teléfono del cliente (guardarraíl del agente) en la capa que use este repositorio.
"""
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from modules.agenda.models import AgendaConfig, Bloqueo, Cita, Disponibilidad, Recurso, Servicio
from modules.agenda.schemas import (
    AgendaConfigCrear,
    BloqueoCrear,
    CitaCrear,
    DisponibilidadCrear,
    RecursoCrear,
    ServicioCrear,
)


class AgendaRepo(Protocol):
    """Puerto de datos del pack (lo implementa `SqlAgendaRepository`; los tests lo falsean)."""

    # --- config que nutre el negocio ---
    async def listar_servicios(self, *, solo_activos: bool = True) -> list[Servicio]: ...
    async def crear_servicio(self, datos: ServicioCrear) -> Servicio: ...
    async def listar_recursos(self, *, solo_activos: bool = True) -> list[Recurso]: ...
    async def crear_recurso(self, datos: RecursoCrear) -> Recurso: ...
    async def asignar_servicio(self, *, recurso_id: int, servicio_id: int) -> None: ...
    async def recursos_de_servicio(self, servicio_id: int) -> list[Recurso]: ...
    async def disponibilidad_de(self, recurso_id: int) -> list[Disponibilidad]: ...
    async def crear_disponibilidad(self, datos: DisponibilidadCrear) -> Disponibilidad: ...
    async def bloqueos_en(
        self, *, inicio: datetime, fin: datetime, recurso_id: int | None = None
    ) -> list[Bloqueo]: ...
    async def crear_bloqueo(self, datos: BloqueoCrear) -> Bloqueo: ...
    async def obtener_config(self) -> AgendaConfig | None: ...
    async def guardar_config(self, datos: AgendaConfigCrear) -> AgendaConfig: ...

    # --- citas (transaccional) ---
    async def citas_de_recurso(
        self, *, recurso_id: int, inicio: datetime, fin: datetime
    ) -> list[Cita]: ...
    async def citas_de_cliente(self, telefono: str) -> list[Cita]: ...
    async def cita_por_key(self, idempotency_key: str) -> Cita | None: ...
    async def crear_cita(self, datos: CitaCrear) -> Cita: ...


class SqlAgendaRepository:
    """Implementación SQL del puerto. Esqueleto: el cuerpo llega con el motor de disponibilidad."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def listar_servicios(self, *, solo_activos: bool = True) -> list[Servicio]:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def crear_servicio(self, datos: ServicioCrear) -> Servicio:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def listar_recursos(self, *, solo_activos: bool = True) -> list[Recurso]:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def crear_recurso(self, datos: RecursoCrear) -> Recurso:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def asignar_servicio(self, *, recurso_id: int, servicio_id: int) -> None:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def recursos_de_servicio(self, servicio_id: int) -> list[Recurso]:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def disponibilidad_de(self, recurso_id: int) -> list[Disponibilidad]:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def crear_disponibilidad(self, datos: DisponibilidadCrear) -> Disponibilidad:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def bloqueos_en(
        self, *, inicio: datetime, fin: datetime, recurso_id: int | None = None
    ) -> list[Bloqueo]:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def crear_bloqueo(self, datos: BloqueoCrear) -> Bloqueo:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def obtener_config(self) -> AgendaConfig | None:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def guardar_config(self, datos: AgendaConfigCrear) -> AgendaConfig:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def citas_de_recurso(
        self, *, recurso_id: int, inicio: datetime, fin: datetime
    ) -> list[Cita]:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def citas_de_cliente(self, telefono: str) -> list[Cita]:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def cita_por_key(self, idempotency_key: str) -> Cita | None:
        raise NotImplementedError("pendiente: motor de disponibilidad")

    async def crear_cita(self, datos: CitaCrear) -> Cita:
        raise NotImplementedError("pendiente: motor de disponibilidad")
