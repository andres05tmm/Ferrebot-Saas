"""Máquinas del vertical construcción y su operación (spec cliente 05_MAQUINAS / 01 — tenant 0043/0045).

Activos que se alquilan/facturan por HORA: cada máquina tiene un `precio_hora_default` sugerido y un
`minimo_horas_factura` (piso facturable por servicio). `costo_operacion_hora` (nullable [DEFINIR]) lo
suma el plan PIM para poder calcular rentabilidad NETA por máquina; no está en la spec original.

En operación (tenant 0045) una máquina se ASIGNA a una obra (`AsignacionMaquinaObra`, con precio y
mínimo pactados que pueden diferir del default), se le registran HORAS por día (`RegistroHorasMaquina`,
donde `horas_facturables` aplica el mínimo) y se le hace MANTENIMIENTO (`Mantenimiento`).

`operador_asignado_id` referencia `trabajadores.id`: siguiendo el patrón del repo (ver
`modules.proveedores`), la FK real vive en la migración (constraint en la BD) y el ORM la mapea como
BigInteger sin `relationship` — no se acopla un módulo a otro para una simple columna. Tablas de negocio
del tenant (sin `empresa_id`: la base ES la frontera). Dinero en MONEY4 (18,4); soft delete `eliminado_en`.
"""
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, Numeric, Text, Time, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY4

# Horas / horómetro: la spec declara TODO Decimal como 18,4.
CANTIDAD = Numeric(18, 4)

# Los tipos los crean las migraciones 0043/0044/0045 (create_type=False): aquí solo se mapean. Literales
# EXACTOS a la spec.
estado_maquina = PgEnum(
    "DISPONIBLE", "OCUPADA", "MANTENIMIENTO", "DAÑADA", "BAJA",
    name="estado_maquina", create_type=False,
)
tipo_mantenimiento = PgEnum(
    "PREVENTIVO", "CORRECTIVO", "INSPECCION", name="tipo_mantenimiento", create_type=False
)
# `origen_registro` es dueño la migración 0044 (reportes_diarios_obra); aquí solo se referencia.
origen_registro = PgEnum(
    "MANUAL", "TELEGRAM_BOT", "IMPORTACION", name="origen_registro", create_type=False
)


class Maquina(TenantBase):
    __tablename__ = "maquinas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    codigo: Mapped[str] = mapped_column(Text, nullable=False, unique=True)   # ej. "M-001"
    nombre: Mapped[str] = mapped_column(Text, nullable=False)  # ej. "Vibrocompactador CAT CS533E"
    tipo: Mapped[str] = mapped_column(Text, nullable=False)    # catálogo de tipos [DEFINIR con cliente]
    placa: Mapped[str | None] = mapped_column(Text)
    serial: Mapped[str | None] = mapped_column(Text)
    anio_fabricacion: Mapped[int | None] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(estado_maquina, nullable=False, server_default="DISPONIBLE")
    # Valor sugerido de facturación por hora y mínimo de horas facturables por servicio.
    precio_hora_default: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    minimo_horas_factura: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    # Costo interno de operar la máquina una hora (combustible/desgaste): para rentabilidad neta [DEFINIR].
    costo_operacion_hora: Mapped[Decimal | None] = mapped_column(MONEY4)
    # FK a `trabajadores.id`: la constraint vive en la migración; el ORM no la modela (patrón del repo).
    operador_asignado_id: Mapped[int | None] = mapped_column(BigInteger)
    foto_url: Mapped[str | None] = mapped_column(Text)
    notas: Mapped[str | None] = mapped_column(Text)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    eliminado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete


class AsignacionMaquinaObra(TenantBase):
    """Máquina puesta en una obra (spec `AsignacionMaquinaObra`, tenant 0045).

    `precio_hora` y `minimo_horas` son POR ASIGNACIÓN: pueden diferir del default de la máquina (lo pactado
    para esa obra). La spec no declara timestamps para esta tabla, así que no se mapean. FKs (maquina/obra/
    operador) viven en la migración; el ORM mapea los ids como BigInteger sin `relationship` (patrón del repo).
    """

    __tablename__ = "asignaciones_maquina_obra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    maquina_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    obra_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date | None] = mapped_column(Date)
    precio_hora: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)   # pactado para esta obra
    minimo_horas: Mapped[int] = mapped_column(Integer, nullable=False)
    operador_id: Mapped[int | None] = mapped_column(BigInteger)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())


class RegistroHorasMaquina(TenantBase):
    """Parte de horas de una máquina por día (spec `RegistroHorasMaquina`, tenant 0045).

    `horas_facturables` = max(horas_trabajadas, minimo) — el mínimo se aplica en el service (Fase 3). Este
    registro es el ancla de idempotencia del cargo a cartera de alquiler (Fase 5).
    """

    __tablename__ = "registros_horas_maquina"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    maquina_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    obra_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    horas_trabajadas: Mapped[Decimal] = mapped_column(CANTIDAD, nullable=False)
    horas_facturables: Mapped[Decimal] = mapped_column(CANTIDAD, nullable=False)
    operador_id: Mapped[int | None] = mapped_column(BigInteger)
    observaciones: Mapped[str | None] = mapped_column(Text)
    origen_registro: Mapped[str] = mapped_column(
        origen_registro, nullable=False, server_default="MANUAL"
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TurnoHorasMaquina(TenantBase):
    """Franja de un operador dentro del parte de horas de un día (rotación de operadores, migración 0054).

    Un `RegistroHorasMaquina` (parte por máquina·obra·día) puede tener VARIOS turnos: la misma máquina rota
    operadores el mismo día (Juan 8:00-13:00, Pedro 14:00-17:00). `horas` es la unidad de negocio (NO se
    deriva de la franja); `hora_inicio`/`hora_fin` son informativos y opcionales. `operador_id` referencia
    `trabajadores.id` (FK en la migración; el ORM la mapea como BigInteger sin `relationship`, patrón del
    repo). El mínimo facturable se aplica UNA vez al total del día en el service (la rotación no multiplica
    el cobro). Borrar el parte arrastra sus turnos (ON DELETE CASCADE en la migración)."""

    __tablename__ = "turnos_horas_maquina"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    registro_horas_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operador_id: Mapped[int | None] = mapped_column(BigInteger)
    hora_inicio: Mapped[time | None] = mapped_column(Time)
    hora_fin: Mapped[time | None] = mapped_column(Time)
    horas: Mapped[Decimal] = mapped_column(CANTIDAD, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Mantenimiento(TenantBase):
    """Mantenimiento de una máquina (spec `Mantenimiento`, tenant 0045).

    Preventivo/correctivo/inspección con costo (MONEY4) y programación del próximo servicio por horómetro
    (`proximo_en_horas`) o por fecha. FK a `proveedores` (quién lo hizo) vive en la migración.
    """

    __tablename__ = "mantenimientos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    maquina_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tipo: Mapped[str] = mapped_column(tipo_mantenimiento, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    horas_maquina: Mapped[Decimal | None] = mapped_column(CANTIDAD)   # horómetro al momento
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    costo: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    proveedor_id: Mapped[int | None] = mapped_column(BigInteger)
    proximo_en_horas: Mapped[Decimal | None] = mapped_column(CANTIDAD)   # preventivos: cada X horas
    proximo_en_fecha: Mapped[date | None] = mapped_column(Date)
    factura_url: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
