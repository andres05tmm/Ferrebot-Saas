"""Modelos del pack Agenda/Citas (fuente de verdad: docs/pack-agenda-citas.md).

Capa 1 (config que nutre el negocio): `servicios`, `recursos`, `recurso_servicio`,
`disponibilidad`, `bloqueos`, `agenda_config`. Capa transaccional (la genera el motor):
`citas`. Todo vive en la base del propio tenant (aislamiento por construcción, sin `empresa_id`).

`recurso` es genérico (`tipo` = profesional/sala/equipo/mesa/cancha) para que el mismo motor
sirva a cualquier vertical reservable (decisión abierta #1, cerrada el 7 jun 2026). Dinero en
NUMERIC; fechas en TIMESTAMPTZ (se operan en hora Colombia, `COLOMBIA_TZ`, regla no negociable #4).
"""
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)

# Los tipos los crea la migración (create_type=False): aquí solo se mapean.
recurso_tipo = PgEnum(
    "profesional", "sala", "equipo", "mesa", "cancha", "habitacion",
    name="recurso_tipo", create_type=False,
)
cita_estado = PgEnum(
    "pendiente", "confirmada", "cumplida", "cancelada", "no_show",
    name="cita_estado", create_type=False,
)
cita_origen = PgEnum("whatsapp", "dashboard", name="cita_origen", create_type=False)
cita_confirmacion = PgEnum(
    "esperando", "reconfirmada", "en_riesgo", name="cita_confirmacion", create_type=False
)
modo_confirmacion = PgEnum("auto", "manual", name="modo_confirmacion", create_type=False)
anticipo_tipo = PgEnum("porcentaje", "fijo", name="anticipo_tipo", create_type=False)


class Servicio(TenantBase):
    """Qué se puede agendar (con duración y buffers que ocupan la agenda)."""

    __tablename__ = "servicios"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    duracion_min: Mapped[int] = mapped_column(Integer, nullable=False)
    precio: Mapped[Decimal | None] = mapped_column(MONEY)
    buffer_antes_min: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    buffer_despues_min: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    categoria: Mapped[str | None] = mapped_column(Text)
    descripcion: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Recurso(TenantBase):
    """Quién/qué presta el servicio. `tipo` genérico para cualquier vertical reservable."""

    __tablename__ = "recursos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    tipo: Mapped[str] = mapped_column(recurso_tipo, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RecursoServicio(TenantBase):
    """N:N — qué recurso presta qué servicio (si hay un solo recurso, se autollena)."""

    __tablename__ = "recurso_servicio"

    recurso_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("recursos.id", ondelete="CASCADE"), primary_key=True
    )
    servicio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("servicios.id", ondelete="CASCADE"), primary_key=True
    )


class Disponibilidad(TenantBase):
    """Horario semanal de cada recurso. Varias filas por día → mañana y tarde."""

    __tablename__ = "disponibilidad"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recurso_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("recursos.id", ondelete="CASCADE"), nullable=False
    )
    dia_semana: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=lunes … 6=domingo
    hora_inicio: Mapped[time] = mapped_column(Time, nullable=False)
    hora_fin: Mapped[time] = mapped_column(Time, nullable=False)


class Bloqueo(TenantBase):
    """Excepciones (ausencias, festivos, citas externas). `recurso_id` null = bloqueo global."""

    __tablename__ = "bloqueos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    recurso_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("recursos.id", ondelete="CASCADE")
    )
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fin: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgendaConfig(TenantBase):
    """Reglas globales del negocio: una sola fila por tenant (id fijo en 1, CHECK en la migración).

    Los campos de anticipo van nullable: el campo se diseña ahora, pero el cobro real se cablea
    cuando exista el frente de pagos (Bre-B/link) — decisión abierta #3.
    """

    __tablename__ = "agenda_config"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, server_default="1")
    zona_horaria: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="America/Bogota"
    )
    intervalo_slots_min: Mapped[int] = mapped_column(Integer, nullable=False, server_default="15")
    anticipacion_minima_min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="120"
    )
    ventana_maxima_dias: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    politica_cancelacion_horas: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="24"
    )
    # Horas antes de la cita para marcar `confirmacion=en_riesgo` si no hubo respuesta (anti-no-show).
    corte_riesgo_horas: Mapped[int] = mapped_column(Integer, nullable=False, server_default="2")
    permite_reagendar: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.true()
    )
    modo_confirmacion: Mapped[str] = mapped_column(
        modo_confirmacion, nullable=False, server_default="auto"
    )
    requiere_anticipo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.false()
    )
    anticipo_tipo: Mapped[str | None] = mapped_column(anticipo_tipo)  # nullable: cobro futuro
    anticipo_valor: Mapped[Decimal | None] = mapped_column(MONEY)     # nullable: cobro futuro
    capacidad_por_slot: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    recordatorios_horas: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default="{24,2}"
    )
    persona: Mapped[str | None] = mapped_column(Text)  # tono/saludo del agente
    # Modo reservas/noches (0022): las horas que convierten "N noches" en [check-in, check-out).
    checkin_hora: Mapped[time] = mapped_column(Time, nullable=False, server_default="15:00")
    checkout_hora: Mapped[time] = mapped_column(Time, nullable=False, server_default="12:00")
    # Sync OPCIONAL con Google Calendar (write-only): id del calendario que el negocio compartió con
    # el service account de plataforma. NULL = sync apagado (la base sigue siendo la fuente de verdad).
    google_calendar_id: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Cita(TenantBase):
    """Transaccional: la genera el motor (no es config). Idempotente por `idempotency_key`.

    El teléfono del cliente = su identidad (su número de WhatsApp). `inicio`/`fin` en TIMESTAMPTZ
    (se operan en hora Colombia). Estado: pendiente → confirmada → cumplida | cancelada | no_show.
    """

    __tablename__ = "citas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    servicio_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("servicios.id"), nullable=False
    )
    recurso_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("recursos.id"), nullable=False)
    cliente_nombre: Mapped[str] = mapped_column(Text, nullable=False)
    cliente_telefono: Mapped[str] = mapped_column(Text, nullable=False)  # identidad del cliente
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fin: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    estado: Mapped[str] = mapped_column(cita_estado, nullable=False, server_default="pendiente")
    origen: Mapped[str] = mapped_column(cita_origen, nullable=False, server_default="whatsapp")
    # Sub-estado de reconfirmación (anti-no-show), paralelo a `estado`: NUNCA libera el cupo.
    confirmacion: Mapped[str] = mapped_column(
        cita_confirmacion, nullable=False, server_default="esperando"
    )
    # Cuándo se envió el recordatorio de reconfirmación (dedup: no reenviar).
    recordatorio_enviado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notas: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    # Cobro de la cita (ADR 0022): la venta que registró el cobro (UNIQUE parcial en la migración
    # 0027 — una cita, una venta) y cuándo se cobró. NULL = aún sin cobrar.
    venta_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("ventas.id"))
    cobrada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Id del evento espejo en Google Calendar (NULL si el sync está apagado o aún no se escribió).
    gcal_event_id: Mapped[str | None] = mapped_column(Text)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
