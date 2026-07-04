"""Modelos ORM de devoluciones (schema.md / tenant 0031).

Tablas de negocio sin `empresa_id`: la base ES la frontera del tenant. `devoluciones` es la cabecera
del reintegro; `devoluciones_detalle` guarda las líneas devueltas con el costo del snapshot de la
SALIDA original (COGS exacto). El vínculo venta↔nota↔devolución se cierra con `venta_id`/`nota_id`.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

MONEY = Numeric(12, 2)
QTY = Numeric(12, 3)


class Devolucion(TenantBase):
    __tablename__ = "devoluciones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venta_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("ventas.id"), nullable=False)
    # Nota crédito ligada (None si la venta no estaba facturada ante DIAN). FK en la base (0031).
    nota_id: Mapped[int | None] = mapped_column(BigInteger)
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # 'efectivo' → egreso de caja; 'fiado' → abono al crédito del cliente.
    metodo_reintegro: Mapped[str] = mapped_column(Text, nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    estado: Mapped[str] = mapped_column(Text, nullable=False, default="registrada")
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    detalles: Mapped[list["DevolucionDetalle"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class DevolucionDetalle(TenantBase):
    __tablename__ = "devoluciones_detalle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    devolucion_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("devoluciones.id", ondelete="CASCADE"), nullable=False
    )
    producto_id: Mapped[int | None] = mapped_column(BigInteger)
    descripcion: Mapped[str | None] = mapped_column(Text)
    cantidad: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # Costo unitario = snapshot de la SALIDA original (COGS exacto; NO el promedio del día).
    costo_unitario: Mapped[Decimal | None] = mapped_column(MONEY)
    total_linea: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
