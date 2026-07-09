"""Modelos de venta (schema.md). Tablas de negocio sin empresa_id: la base es la frontera."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

# Refleja TODOS los valores del enum de Postgres (incl. 'datafono' de la migración 0007 y los
# históricos tarjeta/nequi/daviplata): la columna debe poder escribir 'datafono' y leer los viejos.
# Los métodos vigentes para ventas nuevas los acota `MetodoPago` en schemas (Pydantic), no esta capa.
metodo_pago_enum = PgEnum(
    "efectivo", "transferencia", "tarjeta", "nequi", "daviplata", "fiado", "datafono", "mixto",
    name="metodo_pago", create_type=False,
)
venta_estado_enum = PgEnum("completada", "anulada", name="venta_estado", create_type=False)
venta_origen_enum = PgEnum("web", "bot", "voz", "offline", name="venta_origen", create_type=False)


class Venta(TenantBase):
    __tablename__ = "ventas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consecutivo: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    cliente_id: Mapped[int | None] = mapped_column(BigInteger)
    vendedor_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    impuestos: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    metodo_pago: Mapped[str] = mapped_column(metodo_pago_enum, nullable=False)
    estado: Mapped[str] = mapped_column(venta_estado_enum, nullable=False, default="completada")
    origen: Mapped[str] = mapped_column(venta_origen_enum, nullable=False, default="web")
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)

    detalles: Mapped[list["VentaDetalle"]] = relationship(
        back_populates="venta", cascade="all, delete-orphan", lazy="selectin",
    )


class VentaPago(TenantBase):
    """Una parte del cobro de una venta MIXTA (0053). Solo las ventas con `metodo_pago='mixto'`
    escriben filas aquí; la suma de las partes == total de la venta (lo valida el servicio)."""

    __tablename__ = "ventas_pagos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venta_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ventas.id", ondelete="CASCADE"), nullable=False)
    metodo: Mapped[str] = mapped_column(metodo_pago_enum, nullable=False)
    monto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)


class VentaDetalle(TenantBase):
    __tablename__ = "ventas_detalle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venta_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ventas.id", ondelete="CASCADE"), nullable=False)
    producto_id: Mapped[int | None] = mapped_column(BigInteger)
    descripcion: Mapped[str | None] = mapped_column(Text)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    iva: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    venta: Mapped["Venta"] = relationship(back_populates="detalles")
