"""Modelo de compras fiscales (schema.md / tenant 0001).

Una compra fiscal registra el desglose de IVA (base + iva = total) de una compra a proveedor; alimenta
el Libro IVA (Slice 5). La tabla incluye columnas de RADIAN-FE (Slice 6b): `cufe_proveedor`, las fechas
de los eventos 030-033, `evento_estado` (pendiente/aceptada/reclamada) y `evento_error`. Tabla de
negocio sin `empresa_id`: la base ES la frontera del tenant.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)


class CompraFiscal(TenantBase):
    __tablename__ = "compras_fiscal"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    compra_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("compras.id"))
    proveedor_nit: Mapped[str | None] = mapped_column(Text)
    base: Mapped[Decimal | None] = mapped_column(MONEY)
    iva: Mapped[Decimal | None] = mapped_column(MONEY)
    total: Mapped[Decimal | None] = mapped_column(MONEY)
    soporte_url: Mapped[str | None] = mapped_column(Text)
    # RADIAN-FE (Slice 6b): eventos DIAN sobre la factura recibida del proveedor.
    cufe_proveedor: Mapped[str | None] = mapped_column(Text)
    evento_030_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evento_031_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evento_032_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evento_033_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evento_estado: Mapped[str | None] = mapped_column(Text)
    evento_error: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
