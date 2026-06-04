"""Modelos de catálogo e inventario usados por la venta (schema.md).

Solo las columnas que toca la Fase 1; el resto del esquema existe en la base vía migración.
"""
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

mov_inventario_tipo = PgEnum(
    "ENTRADA", "SALIDA", "AJUSTE", "DEVOLUCION",
    name="mov_inventario_tipo", create_type=False,
)


class Producto(TenantBase):
    __tablename__ = "productos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    codigo: Mapped[str | None] = mapped_column(Text)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    unidad_medida: Mapped[str] = mapped_column(Text, nullable=False)
    precio_venta: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    iva: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    permite_fraccion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Inventario(TenantBase):
    __tablename__ = "inventario"

    producto_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_actual: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    stock_minimo: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)


class MovimientoInventario(TenantBase):
    __tablename__ = "movimientos_inventario"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tipo: Mapped[str] = mapped_column(mov_inventario_tipo, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    costo_unitario: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    referencia: Mapped[str | None] = mapped_column(Text)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
