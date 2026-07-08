"""Herramientas del vertical construcción (spec cliente 06_HERRAMIENTAS — tenant 0043).

CRUD ligero: activos menores (no se facturan por hora como las máquinas), con `cantidad` y una
`ubicacion_actual` de texto libre (obra o bodega). Tabla de negocio del tenant (sin `empresa_id`: la
base ES la frontera). `valor_reposicion` en MONEY4 (18,4); soft delete `eliminado_en`.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY4

# El tipo lo crea la migración 0043 (create_type=False): aquí solo se mapea. Literales EXACTOS a la spec.
estado_herramienta = PgEnum(
    "DISPONIBLE", "EN_OBRA", "MANTENIMIENTO", "PERDIDA", "BAJA",
    name="estado_herramienta", create_type=False,
)


class Herramienta(TenantBase):
    __tablename__ = "herramientas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    codigo: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    categoria: Mapped[str | None] = mapped_column(Text)  # catálogo de categorías [DEFINIR con cliente]
    cantidad: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    ubicacion_actual: Mapped[str | None] = mapped_column(Text)  # obra o bodega
    estado: Mapped[str] = mapped_column(
        estado_herramienta, nullable=False, server_default="DISPONIBLE"
    )
    valor_reposicion: Mapped[Decimal | None] = mapped_column(MONEY4)
    notas: Mapped[str | None] = mapped_column(Text)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    eliminado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete
