"""Schemas Pydantic del pack reservas (REST del dashboard/recepción).

Una reserva ES una cita sobre un recurso tipo `habitacion` (ver `service.py`); por eso `ReservaLeer`
reusa `CitaLeer` del pack agenda y le añade el `replay` (idempotencia) y el `anticipo` a cobrar.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from modules.agenda.schemas import CitaLeer


class HabitacionLibreLeer(BaseModel):
    """Una habitación ofrecible para el rango pedido, con su precio/noche y el total (si hay tarifa)."""

    recurso_id: int
    nombre: str
    precio_noche: Decimal | None
    total: Decimal | None


class ReservaCrear(BaseModel):
    """Alta de reserva desde el dashboard/recepción. `noches` acotado (1..30, igual que el motor)."""

    recurso_id: int
    checkin: date
    noches: int = Field(ge=1, le=30)
    cliente_nombre: str = Field(min_length=1)
    cliente_telefono: str = Field(min_length=1)
    idempotency_key: str | None = None


class ReservaLeer(BaseModel):
    """Reserva creada: la cita subyacente + si fue replay idempotente + el anticipo a cobrar (o None)."""

    cita: CitaLeer
    replay: bool
    anticipo: Decimal | None
