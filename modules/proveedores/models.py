"""Modelos de cuentas por pagar a proveedor (schema.md / tenant 0001).

`facturas_proveedores.id` es el número de factura DEL proveedor (TEXT, PK natural). `pagado`/`pendiente`/
`estado` son derivados de los abonos: el servicio los recalcula al registrar un abono. Tabla de negocio
sin `empresa_id`: la base ES la frontera del tenant.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)


class FacturaProveedor(TenantBase):
    __tablename__ = "facturas_proveedores"

    id: Mapped[str] = mapped_column(Text, primary_key=True)   # nº de factura del proveedor
    proveedor: Mapped[str] = mapped_column(Text, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    pagado: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default="0")
    pendiente: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, server_default="pendiente")
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    foto_url: Mapped[str | None] = mapped_column(Text)
    foto_nombre: Mapped[str | None] = mapped_column(Text)
    # FK a usuarios existe en la BD (migración); el ORM no la modela (no hay modelo Usuario), como caja/ventas.
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AbonoProveedor(TenantBase):
    __tablename__ = "facturas_abonos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    factura_id: Mapped[str] = mapped_column(
        Text, ForeignKey("facturas_proveedores.id", ondelete="CASCADE")
    )
    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    foto_url: Mapped[str | None] = mapped_column(Text)
    foto_nombre: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
