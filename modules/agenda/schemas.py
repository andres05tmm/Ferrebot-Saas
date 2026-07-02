"""Contratos Pydantic del pack Agenda/Citas (crear/leer). Fuente: docs/pack-agenda-citas.md.

`*Crear` = entrada validada (lo que nutre el negocio / lo que pide el motor). `*Leer` =
proyección de lectura (`from_attributes`). El motor de disponibilidad, los endpoints y las
herramientas del agente NO van aquí — llegan en prompts siguientes.
"""
from datetime import datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RecursoTipo = Literal["profesional", "sala", "equipo", "mesa", "cancha", "habitacion"]
CitaEstado = Literal["pendiente", "confirmada", "cumplida", "cancelada", "no_show"]
CitaOrigen = Literal["whatsapp", "dashboard"]
ModoConfirmacion = Literal["auto", "manual"]
AnticipoTipo = Literal["porcentaje", "fijo"]


# --- cobro de cita (ADR 0022) -------------------------------------------------
# `fiado` queda fuera de v1 (requiere cliente_id del POS; la identidad de agenda es el teléfono).
CobroMetodoPago = Literal["efectivo", "transferencia", "datafono"]


class CitaCobrar(BaseModel):
    metodo_pago: CobroMetodoPago
    # Override del precio del servicio (p. ej. reservas por noches, ADR 0022 §D6).
    precio_override: Decimal | None = Field(default=None, gt=0)


class CobroLeer(BaseModel):
    venta_id: int
    total: Decimal
    replay: bool  # True = la cita ya estaba cobrada; misma venta, sin duplicar


# --- servicios ---------------------------------------------------------------
class ServicioCrear(BaseModel):
    nombre: str = Field(min_length=1)
    duracion_min: int = Field(gt=0)
    precio: Decimal | None = Field(default=None, ge=0)
    buffer_antes_min: int = Field(default=0, ge=0)
    buffer_despues_min: int = Field(default=0, ge=0)
    categoria: str | None = None
    descripcion: str | None = None
    activo: bool = True


class ServicioLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    duracion_min: int
    precio: Decimal | None
    buffer_antes_min: int
    buffer_despues_min: int
    categoria: str | None
    descripcion: str | None
    activo: bool
    creado_en: datetime


# --- recursos ----------------------------------------------------------------
class RecursoCrear(BaseModel):
    nombre: str = Field(min_length=1)
    tipo: RecursoTipo
    activo: bool = True


class RecursoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    tipo: str
    activo: bool
    creado_en: datetime


# --- recurso_servicio (N:N) --------------------------------------------------
class RecursoServicioCrear(BaseModel):
    recurso_id: int
    servicio_id: int


class RecursoServicioLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recurso_id: int
    servicio_id: int


# --- disponibilidad ----------------------------------------------------------
class DisponibilidadCrear(BaseModel):
    recurso_id: int
    dia_semana: int = Field(ge=0, le=6)  # 0=lunes … 6=domingo
    hora_inicio: time
    hora_fin: time


class DisponibilidadLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recurso_id: int
    dia_semana: int
    hora_inicio: time
    hora_fin: time


# --- bloqueos ----------------------------------------------------------------
class BloqueoCrear(BaseModel):
    recurso_id: int | None = None  # null = bloqueo global del negocio
    inicio: datetime
    fin: datetime
    motivo: str | None = None


class BloqueoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recurso_id: int | None
    inicio: datetime
    fin: datetime
    motivo: str | None
    creado_en: datetime


# --- agenda_config -----------------------------------------------------------
class AgendaConfigCrear(BaseModel):
    """Reglas del negocio. Todo con default salvo lo de anticipo (cobro futuro, opcional)."""

    zona_horaria: str = "America/Bogota"
    intervalo_slots_min: int = Field(default=15, gt=0)
    anticipacion_minima_min: int = Field(default=120, ge=0)
    ventana_maxima_dias: int = Field(default=30, gt=0)
    politica_cancelacion_horas: int = Field(default=24, ge=0)
    corte_riesgo_horas: int = Field(default=2, ge=0)  # horas antes para marcar en_riesgo sin respuesta
    permite_reagendar: bool = True
    modo_confirmacion: ModoConfirmacion = "auto"
    requiere_anticipo: bool = False
    anticipo_tipo: AnticipoTipo | None = None
    anticipo_valor: Decimal | None = Field(default=None, ge=0)
    capacidad_por_slot: int = Field(default=1, gt=0)
    recordatorios_horas: list[int] = Field(default_factory=lambda: [24, 2])
    persona: str | None = None
    # Modo reservas/noches (0022): horas de check-in/check-out de los recursos tipo habitación.
    checkin_hora: time = time(15, 0)
    checkout_hora: time = time(12, 0)
    # Sync opcional con Google Calendar: calendar_id compartido con el service account. None = apagado.
    google_calendar_id: str | None = None


class AgendaConfigLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    zona_horaria: str
    intervalo_slots_min: int
    anticipacion_minima_min: int
    ventana_maxima_dias: int
    politica_cancelacion_horas: int
    corte_riesgo_horas: int
    permite_reagendar: bool
    modo_confirmacion: str
    requiere_anticipo: bool
    anticipo_tipo: str | None
    anticipo_valor: Decimal | None
    capacidad_por_slot: int
    recordatorios_horas: list[int]
    persona: str | None
    checkin_hora: time
    checkout_hora: time
    google_calendar_id: str | None
    creado_en: datetime
    actualizado_en: datetime | None


# --- citas -------------------------------------------------------------------
class CitaCrear(BaseModel):
    """Lo que pide el motor al agendar. `fin` lo computa el motor (duración + buffers)."""

    servicio_id: int
    recurso_id: int
    cliente_nombre: str = Field(min_length=1)
    cliente_telefono: str = Field(min_length=1)
    inicio: datetime
    fin: datetime
    origen: CitaOrigen = "whatsapp"
    notas: str | None = None
    idempotency_key: str | None = None


class CitaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    servicio_id: int
    recurso_id: int
    cliente_nombre: str
    cliente_telefono: str
    inicio: datetime
    fin: datetime
    estado: str
    origen: str
    confirmacion: str
    recordatorio_enviado_en: datetime | None
    notas: str | None
    idempotency_key: str | None
    # Cobro (ADR 0022): venta vinculada y cuándo se cobró; NULL = sin cobrar (la UI decide el botón).
    venta_id: int | None = None
    cobrada_en: datetime | None = None
    gcal_event_id: str | None
    creada_en: datetime


# --- dashboard (acciones del negocio) ----------------------------------------
class CitaManualCrear(BaseModel):
    """Alta de cita desde el dashboard (origen=dashboard). El motor computa `fin` y valida el cupo."""

    servicio_id: int
    recurso_id: int
    inicio: datetime
    cliente_nombre: str = Field(min_length=1)
    cliente_telefono: str = Field(min_length=1)
    notas: str | None = None


class ReagendarPayload(BaseModel):
    """Nuevo inicio para reagendar una cita (el motor recalcula `fin` y revalida el cupo)."""

    nuevo_inicio: datetime


class SlotLeer(BaseModel):
    """Un cupo libre ofrecible por el motor: inicio (hora Colombia) + recurso que lo prestaría."""

    inicio: datetime
    recurso_id: int
