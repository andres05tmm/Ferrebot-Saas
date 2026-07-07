"""Modelos de compras a proveedor y proveedores (schema.md / tenant 0001, `Proveedor` extendido en 0046).

Tablas de negocio sin `empresa_id`: la base ES la frontera del tenant. Una compra suma stock por sus
movimientos de inventario (regla #7) y fija el costo de compra del producto; el detalle se borra en
cascada con la compra. El CRUD completo de proveedores + cuentas por pagar es el Slice 4b.

El vertical construcción (spec cliente 10) suma a `Proveedor` un `tipo` (planta de asfalto, cantera,
repuestos…) para el análisis de precios por rubro, y datos de `contacto_*`. Son columnas NULLABLE
agregadas al final por la migración 0046 (backward-compatible); el enum `tipo_proveedor` lo crea esa
migración (create_type=False). Nota: la entidad `Proveedor` se mapea AQUÍ (no en `modules.proveedores`,
que solo tiene cuentas por pagar), así que la extensión del ORM vive en este archivo.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

MONEY = Numeric(12, 2)
QTY = Numeric(12, 3)

# El tipo lo crea la migración 0046 (create_type=False): aquí solo se mapea. Literales EXACTOS a la spec.
tipo_proveedor = PgEnum(
    "PLANTA_ASFALTO", "CANTERA_ARENA", "REPUESTOS", "COMBUSTIBLE", "TRANSPORTE", "SERVICIOS", "OTRO",
    name="tipo_proveedor", create_type=False,
)


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

    # --- Vertical construcción (spec 10 / tenant 0046). Columnas nullable. ---
    tipo: Mapped[str | None] = mapped_column(tipo_proveedor)
    contacto_nombre: Mapped[str | None] = mapped_column(Text)
    contacto_telefono: Mapped[str | None] = mapped_column(Text)
    contacto_email: Mapped[str | None] = mapped_column(Text)


class Compra(TenantBase):
    __tablename__ = "compras"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proveedor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("proveedores.id")
    )
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    # Idempotencia estructural (ai-tools.md §4): UNIQUE parcial (WHERE NOT NULL) creado en la migración
    # 0025. Un reintento con la misma key no duplica la compra ni sus ENTRADAS de inventario.
    idempotency_key: Mapped[str | None] = mapped_column(Text)
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
