"""Modelos de compras a proveedor y proveedores (schema.md / tenant 0001).

Tablas de negocio sin `empresa_id`: la base ES la frontera del tenant. Una compra suma stock por sus
movimientos de inventario (regla #7) y fija el costo de compra del producto; el detalle se borra en
cascada con la compra. El CRUD completo de proveedores + cuentas por pagar es el Slice 4b.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

MONEY = Numeric(12, 2)
QTY = Numeric(12, 3)


class Proveedor(TenantBase):
    __tablename__ = "proveedores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    nit: Mapped[str | None] = mapped_column(Text)
    telefono: Mapped[str | None] = mapped_column(Text)
    correo: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Compra(TenantBase):
    __tablename__ = "compras"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proveedor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("proveedores.id")
    )
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    detalles: Mapped[list["CompraDetalle"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class CompraDetalle(TenantBase):
    __tablename__ = "compras_detalle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    compra_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("compras.id", ondelete="CASCADE"), nullable=False
    )
    producto_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("productos.id"))
    cantidad: Mapped[Decimal | None] = mapped_column(QTY)
    costo: Mapped[Decimal | None] = mapped_column(MONEY)
