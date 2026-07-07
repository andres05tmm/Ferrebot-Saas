"""Trabajadores del vertical construcción y su operación (spec cliente 07_EMPLEADOS / 01 — tenant
0043/0045).

`Trabajador` es la persona que ejecuta obra; es distinto de `Usuario` (auth/RBAC): un operador puede
existir en nómina sin cuenta en el dashboard. Dos naturalezas conviven en la misma tabla según
`tipo_vinculacion` (spec): DIRECTO (planta: salario base, prestaciones, aportes) vs PATACALIENTE (por
hora: `tarifa_hora`, sin deducciones ni nómina electrónica). Los literales del enum se conservan EXACTOS
como en la spec (mayúsculas). Tabla de negocio del tenant (sin `empresa_id`: la base ES la frontera).

En operación (tenant 0045) un trabajador se ASIGNA a una obra (`AsignacionTrabajadorObra`) y se le lleva
ASISTENCIA por día (`RegistroAsistencia`, con horas extra y ausencias) — insumo de la liquidación de
nómina y su prorrateo por obra (Fase 4).

`activo` (spec) y `eliminado_en` (soft delete del plan PIM) conviven: `activo=false` es una baja
laboral reversible; `eliminado_en` es la ocultación del registro. Dinero en MONEY4 (18,4).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY4

# Horas trabajadas / extra: la spec declara TODO Decimal como 18,4.
CANTIDAD = Numeric(18, 4)

# Los tipos los crean las migraciones 0043/0044/0045 (create_type=False): aquí solo se mapean. Literales
# EXACTOS a la spec.
tipo_vinculacion = PgEnum("DIRECTO", "PATACALIENTE", name="tipo_vinculacion", create_type=False)
tipo_ausencia = PgEnum(
    "INCAPACIDAD", "LICENCIA_REMUNERADA", "LICENCIA_NO_REMUNERADA", "VACACIONES",
    "FALTA_INJUSTIFICADA", name="tipo_ausencia", create_type=False,
)
# `origen_registro` es dueño la migración 0044 (reportes_diarios_obra); aquí solo se referencia.
origen_registro = PgEnum(
    "MANUAL", "TELEGRAM_BOT", "IMPORTACION", name="origen_registro", create_type=False
)


class Trabajador(TenantBase):
    __tablename__ = "trabajadores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tipo_vinculacion: Mapped[str] = mapped_column(tipo_vinculacion, nullable=False)
    documento: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    tipo_documento: Mapped[str] = mapped_column(Text, nullable=False, server_default="CC")
    nombres: Mapped[str] = mapped_column(Text, nullable=False)
    apellidos: Mapped[str] = mapped_column(Text, nullable=False)
    telefono: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    direccion: Mapped[str | None] = mapped_column(Text)
    cargo: Mapped[str] = mapped_column(Text, nullable=False)  # ej. "Operador vibrocompactador"
    fecha_ingreso: Mapped[date | None] = mapped_column(Date)
    fecha_retiro: Mapped[date | None] = mapped_column(Date)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())

    # Solo DIRECTO: salario y datos de seguridad social/bancarios (nullable para PATACALIENTE).
    salario_base: Mapped[Decimal | None] = mapped_column(MONEY4)
    aplica_aux_transporte: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.true()
    )
    eps: Mapped[str | None] = mapped_column(Text)
    fondo_pension: Mapped[str | None] = mapped_column(Text)
    arl: Mapped[str | None] = mapped_column(Text)
    caja_compensacion: Mapped[str | None] = mapped_column(Text)
    cuenta_bancaria: Mapped[str | None] = mapped_column(Text)
    banco_nombre: Mapped[str | None] = mapped_column(Text)

    # Solo PATACALIENTE: tarifa por hora (nullable para DIRECTO).
    tarifa_hora: Mapped[Decimal | None] = mapped_column(MONEY4)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    eliminado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete


class AsignacionTrabajadorObra(TenantBase):
    """Trabajador puesto en una obra (spec `AsignacionTrabajadorObra`, tenant 0045).

    La spec no declara timestamps para esta tabla, así que no se mapean. FKs (trabajador/obra) viven en la
    migración; el ORM mapea los ids como BigInteger sin `relationship` (patrón del repo).
    """

    __tablename__ = "asignaciones_trabajador_obra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trabajador_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    obra_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date | None] = mapped_column(Date)
    activa: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())


class RegistroAsistencia(TenantBase):
    """Día de trabajo de un trabajador (spec `RegistroAsistencia`, tenant 0045).

    Insumo de la liquidación de nómina: horas del día + extras (diurnas/nocturnas/dominical-festivo) o una
    `ausencia`. `obra_id` NULL = día administrativo/no imputable a obra (afecta el prorrateo, Fase 4).
    """

    __tablename__ = "registros_asistencia"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trabajador_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    obra_id: Mapped[int | None] = mapped_column(BigInteger)   # NULL = administrativo
    horas_trabajadas: Mapped[Decimal] = mapped_column(CANTIDAD, nullable=False, server_default="8")
    horas_extra_diurnas: Mapped[Decimal] = mapped_column(
        CANTIDAD, nullable=False, server_default="0"
    )
    horas_extra_nocturnas: Mapped[Decimal] = mapped_column(
        CANTIDAD, nullable=False, server_default="0"
    )
    horas_dominical_festivo: Mapped[Decimal] = mapped_column(
        CANTIDAD, nullable=False, server_default="0"
    )
    ausencia: Mapped[str | None] = mapped_column(tipo_ausencia)
    observaciones: Mapped[str | None] = mapped_column(Text)
    origen_registro: Mapped[str] = mapped_column(
        origen_registro, nullable=False, server_default="MANUAL"
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
