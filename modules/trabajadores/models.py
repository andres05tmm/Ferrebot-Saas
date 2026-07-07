"""Trabajadores del vertical construcción (spec cliente 07_EMPLEADOS — tenant 0043).

`Trabajador` es la persona que ejecuta obra; es distinto de `Usuario` (auth/RBAC): un operador puede
existir en nómina sin cuenta en el dashboard. Dos naturalezas conviven en la misma tabla según
`tipo_vinculacion` (spec): DIRECTO (planta: salario base, prestaciones, aportes) vs PATACALIENTE (por
hora: `tarifa_hora`, sin deducciones ni nómina electrónica). Los literales del enum se conservan EXACTOS
como en la spec (mayúsculas). Tabla de negocio del tenant (sin `empresa_id`: la base ES la frontera).

`activo` (spec) y `eliminado_en` (soft delete del plan PIM) conviven: `activo=false` es una baja
laboral reversible; `eliminado_en` es la ocultación del registro. Dinero en MONEY4 (18,4).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY4

# El tipo lo crea la migración 0043 (create_type=False): aquí solo se mapea. Literales EXACTOS a la spec.
tipo_vinculacion = PgEnum("DIRECTO", "PATACALIENTE", name="tipo_vinculacion", create_type=False)


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
