"""Modelos del pack ventas/cotizaciones (ADR 0017 / tenant 0020).

El catálogo y los precios viven en el POS (solo se LEEN); aquí vive la cotización con snapshot de
nombre/precio por ítem (la emitida no cambia aunque el catálogo cambie — por eso tiene vigencia).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

MONEY = Numeric(12, 2)

cotizacion_estado = PgEnum(
    "abierta", "emitida", "aceptada", "vencida", "cancelada",
    name="cotizacion_estado", create_type=False,
)


class VentasWaConfig(TenantBase):
    __tablename__ = "ventas_wa_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    mostrar_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    vigencia_dias: Mapped[int] = mapped_column(Integer, nullable=False, default=3)


class Cotizacion(TenantBase):
    __tablename__ = "cotizaciones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_telefono: Mapped[str] = mapped_column(Text, nullable=False)
    cliente_nombre: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(cotizacion_estado, nullable=False, default="abierta")
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    vigencia_hasta: Mapped[date | None] = mapped_column(Date)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["CotizacionItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class CotizacionItem(TenantBase):
    __tablename__ = "cotizacion_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cotizacion_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cotizaciones.id", ondelete="CASCADE"), nullable=False
    )
    producto_id: Mapped[int | None] = mapped_column(BigInteger)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
