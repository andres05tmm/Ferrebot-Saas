"""Máquinas del vertical construcción (spec cliente 05_MAQUINAS — tenant 0043).

Activos que se alquilan/facturan por HORA: cada máquina tiene un `precio_hora_default` sugerido y un
`minimo_horas_factura` (piso facturable por servicio). `costo_operacion_hora` (nullable [DEFINIR]) lo
suma el plan PIM para poder calcular rentabilidad NETA por máquina; no está en la spec original.

`operador_asignado_id` referencia `trabajadores.id`: siguiendo el patrón del repo (ver
`modules.proveedores`), la FK real vive en la migración (constraint en la BD) y el ORM la mapea como
BigInteger sin `relationship` — no se acopla un módulo a otro para una simple columna. Tabla de negocio
del tenant (sin `empresa_id`: la base ES la frontera). Dinero en MONEY4 (18,4); soft delete `eliminado_en`.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY4

# El tipo lo crea la migración 0043 (create_type=False): aquí solo se mapea. Literales EXACTOS a la spec.
estado_maquina = PgEnum(
    "DISPONIBLE", "OCUPADA", "MANTENIMIENTO", "DAÑADA", "BAJA",
    name="estado_maquina", create_type=False,
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
