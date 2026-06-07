"""Errores de dominio del pack Agenda/Citas (el router/herramientas los mapean a HTTP/mensaje).

`alternativas` viaja en `CupoNoDisponible` para que el agente le ofrezca otros cupos al cliente
cuando el pedido choca (doble-reserva o cupo inválido) — el motor nunca calcula en el agente.
"""
from datetime import datetime


class AgendaError(Exception):
    """Base de los errores del pack Agenda/Citas."""


class ServicioInexistente(AgendaError):
    def __init__(self, servicio_id: int) -> None:
        super().__init__(f"El servicio {servicio_id} no existe o está inactivo")
        self.servicio_id = servicio_id


class RecursoInexistente(AgendaError):
    def __init__(self, recurso_id: int) -> None:
        super().__init__(f"El recurso {recurso_id} no existe o está inactivo")
        self.recurso_id = recurso_id


class RecursoNoPrestaServicio(AgendaError):
    """El recurso pedido no está asignado al servicio (recurso_servicio)."""

    def __init__(self, recurso_id: int, servicio_id: int) -> None:
        super().__init__(f"El recurso {recurso_id} no presta el servicio {servicio_id}")
        self.recurso_id = recurso_id
        self.servicio_id = servicio_id


class CitaInexistente(AgendaError):
    def __init__(self, cita_id: int) -> None:
        super().__init__(f"La cita {cita_id} no existe")
        self.cita_id = cita_id


class CupoNoDisponible(AgendaError):
    """El cupo pedido no se puede agendar (ocupado, fuera de horario o viola una regla).

    Lleva hasta unas pocas `alternativas` (otros inicios libres) para que el agente las ofrezca.
    """

    def __init__(self, inicio: datetime, alternativas: list[datetime] | None = None) -> None:
        super().__init__(f"El cupo {inicio.isoformat()} no está disponible")
        self.inicio = inicio
        self.alternativas = alternativas or []


class ReagendarNoPermitido(AgendaError):
    """El negocio tiene `permite_reagendar = false`."""

    def __init__(self) -> None:
        super().__init__("El negocio no permite reagendar citas")


class FueraDePoliticaCancelacion(AgendaError):
    """Falta menos de `politica_cancelacion_horas` para la cita: no se cancela/reagenda sin fricción."""

    def __init__(self, horas_requeridas: int) -> None:
        super().__init__(
            f"Cancelar o reagendar exige al menos {horas_requeridas} h de anticipación"
        )
        self.horas_requeridas = horas_requeridas


class CitaNoModificable(AgendaError):
    """La cita ya está en un estado terminal (cumplida/cancelada/no_show)."""

    def __init__(self, cita_id: int, estado: str) -> None:
        super().__init__(f"La cita {cita_id} está '{estado}' y no se puede modificar")
        self.cita_id = cita_id
        self.estado = estado
