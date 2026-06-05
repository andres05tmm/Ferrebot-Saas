"""Modelo de compras fiscales (schema.md / tenant 0001).

Una compra fiscal registra el desglose de IVA (base + iva = total) de una compra a proveedor; alimenta
el Libro IVA (Slice 5). La tabla `compras_fiscal` ya existe e incluye columnas de RADIAN-FE
(`cufe_proveedor`, `evento_030_at`…`evento_033_at`, `evento_estado`, `evento_error`) que en este slice
(6a, solo DATOS) se DEJAN sin mapear → quedan NULL. RADIAN es el Slice 6b (diferido). Tabla de negocio
sin `empresa_id`: la base ES la frontera del tenant.
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
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Columnas RADIAN (cufe_proveedor, evento_030_at…033_at, evento_estado, evento_error) NO se modelan
    # aquí a propósito: el Slice 6a es solo datos. Las usará el Slice 6b (RADIAN-FE recibidas, diferido).
