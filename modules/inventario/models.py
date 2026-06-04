"""Modelos de catálogo e inventario usados por la venta (schema.md).

Solo las columnas que toca la Fase 1; el resto del esquema existe en la base vía migración.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    categoria: Mapped[str | None] = mapped_column(Text)
    marca: Mapped[str | None] = mapped_column(Text)
    unidad_medida: Mapped[str] = mapped_column(Text, nullable=False)
    precio_venta: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_compra: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    precio_mayorista: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Precio escalonado por cantidad (modelo FerreBot): NULL si no aplica.
    precio_umbral: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    precio_bajo_umbral: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    precio_sobre_umbral: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    iva: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    permite_fraccion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False)

    fracciones: Mapped[list["ProductoFraccion"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class ProductoFraccion(TenantBase):
    __tablename__ = "productos_fracciones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False
    )
    fraccion: Mapped[str] = mapped_column(Text, nullable=False)
    decimal: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    precio_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_unitario: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))


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
    # Idempotencia estructural (migración 0002): UNIQUE parcial donde no es NULL.
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
