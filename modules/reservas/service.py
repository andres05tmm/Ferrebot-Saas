"""Motor del pack reservas (plan §2.7): el motor de agenda con otra cara — noches en vez de slots.

NO es un pack con tablas propias: una reserva ES una cita (`citas`) sobre un recurso tipo
`habitacion`, con `inicio = check-in` y `fin = check-out` (horas de `agenda_config`). La
disponibilidad es la resta de ocupaciones del repo de agenda (citas activas + bloqueos); el
anti-doble-reserva es el mismo advisory lock por recurso. Mis-reservas/cancelar son las
herramientas de agenda de siempre (mis_citas/cancelar_cita): una cita es una cita.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from core.config.timezone import COLOMBIA_TZ
from modules.agenda.errors import CupoNoDisponible, RecursoInexistente
from modules.agenda.models import Cita, Recurso
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import CitaCrear

# Tope del horizonte de una reserva (no configurable: evita bloquear una habitación por meses).
_MAX_NOCHES = 30


class NochesInvalidas(Exception):
    """Cantidad de noches fuera de rango (1..30)."""


@dataclass(frozen=True, slots=True)
class HabitacionLibre:
    """Una habitación ofrecible para el rango pedido (con su precio por noche si está definido)."""

    recurso_id: int
    nombre: str
    precio_noche: Decimal | None
    total: Decimal | None       # precio_noche × noches (None si no hay precio definido)


@dataclass(frozen=True, slots=True)
class ResultadoReserva:
    """Reserva creada. `anticipo` = monto a cobrar para confirmar (None si no se exige)."""

    cita: Cita
    replay: bool
    anticipo: Decimal | None


class ReservasService:
    def __init__(self, repo: SqlAgendaRepository) -> None:
        self._repo = repo

    async def _rango(self, checkin: date, noches: int) -> tuple[datetime, datetime]:
        """[check-in, check-out) con las horas de `agenda_config` (hora Colombia)."""
        config = await self._repo.obtener_config()
        h_in = config.checkin_hora if config else None
        h_out = config.checkout_hora if config else None
        from datetime import time as dt_time
        inicio = datetime.combine(checkin, h_in or dt_time(15, 0), tzinfo=COLOMBIA_TZ)
        fin = datetime.combine(
            checkin + timedelta(days=noches), h_out or dt_time(12, 0), tzinfo=COLOMBIA_TZ
        )
        return inicio, fin

    async def _precio_noche(self, recurso_id: int) -> Decimal | None:
        """Precio por noche = el precio del (primer) servicio que presta la habitación."""
        servicios = await self._repo.servicios_de_recurso(recurso_id)
        for servicio in servicios:
            if servicio.precio is not None:
                return servicio.precio
        return None

    async def habitaciones_libres(self, checkin: date, noches: int) -> list[HabitacionLibre]:
        """Habitaciones (recursos tipo `habitacion`) SIN ocupación en [check-in, check-out)."""
        if not 1 <= noches <= _MAX_NOCHES:
            raise NochesInvalidas(str(noches))
        inicio, fin = await self._rango(checkin, noches)
        libres: list[HabitacionLibre] = []
        for recurso in await self._repo.listar_recursos():
            if recurso.tipo != "habitacion":
                continue
            ocupado = await self._repo.ocupaciones_de_recurso(
                recurso_id=recurso.id, inicio=inicio, fin=fin
            )
            if ocupado:
                continue
            precio = await self._precio_noche(recurso.id)
            libres.append(HabitacionLibre(
                recurso_id=recurso.id, nombre=recurso.nombre, precio_noche=precio,
                total=precio * noches if precio is not None else None,
            ))
        return libres

    async def reservar(
        self,
        *,
        recurso_id: int,
        checkin: date,
        noches: int,
        cliente_nombre: str,
        cliente_telefono: str,
        idempotency_key: str | None = None,
        origen: str = "whatsapp",
    ) -> ResultadoReserva:
        """Reserva la habitación N noches: lock por recurso + revalidación bajo lock + cita.

        Estado: `pendiente` si el negocio exige anticipo (`requiere_anticipo`) — se confirma al
        pagar (frente de pagos) o a mano; si no, según `modo_confirmacion` (auto → confirmada).
        """
        if not 1 <= noches <= _MAX_NOCHES:
            raise NochesInvalidas(str(noches))
        if idempotency_key:
            existente = await self._repo.cita_por_key(idempotency_key)
            if existente is not None:
                return ResultadoReserva(cita=existente, replay=True, anticipo=None)

        recurso = await self._repo.recurso_por_id(recurso_id)
        if recurso is None or recurso.tipo != "habitacion" or not recurso.activo:
            raise RecursoInexistente(f"Habitación {recurso_id} no existe.")
        inicio, fin = await self._rango(checkin, noches)

        # Anti-doble-reserva: mismo patrón del motor de agenda (lock + revalidar bajo el lock).
        await self._repo.lock_recurso(recurso_id)
        if await self._repo.ocupaciones_de_recurso(recurso_id=recurso_id, inicio=inicio, fin=fin):
            raise CupoNoDisponible(inicio=inicio, alternativas=[])

        config = await self._repo.obtener_config()
        requiere_anticipo = bool(config and config.requiere_anticipo)
        modo = config.modo_confirmacion if config else "auto"
        estado = "pendiente" if requiere_anticipo or modo == "manual" else "confirmada"

        # La cita necesita un servicio: el que presta la habitación (el precio por noche vive ahí).
        servicios = await self._repo.servicios_de_recurso(recurso_id)
        if not servicios:
            raise RecursoInexistente(
                f"La habitación {recurso_id} no tiene un servicio/tarifa asignado."
            )
        cita = await self._repo.crear_cita(
            CitaCrear(
                servicio_id=servicios[0].id, recurso_id=recurso_id,
                cliente_nombre=cliente_nombre, cliente_telefono=cliente_telefono,
                inicio=inicio, fin=fin, origen=origen,
                notas=f"Reserva {noches} noche(s), check-in {checkin}",
                idempotency_key=idempotency_key,
            ),
            estado=estado, fin=fin,
        )
        anticipo = self._calcular_anticipo(config, await self._precio_noche(recurso_id), noches)
        return ResultadoReserva(cita=cita, replay=False, anticipo=anticipo)

    @staticmethod
    def _calcular_anticipo(config, precio_noche: Decimal | None, noches: int) -> Decimal | None:
        """Monto del anticipo según `agenda_config` (None = no se exige o no hay tarifa base)."""
        if config is None or not config.requiere_anticipo or precio_noche is None:
            return None
        total = precio_noche * noches
        if config.anticipo_tipo == "fijo" and config.anticipo_valor is not None:
            return config.anticipo_valor
        if config.anticipo_tipo == "porcentaje" and config.anticipo_valor is not None:
            return (total * config.anticipo_valor / Decimal("100")).quantize(Decimal("1"))
        return None
