"""Modelos ORM de retenciones/INC (ADR 0027, tenant 0032/0033).

Tablas de negocio sin `empresa_id`: la base ES la frontera del tenant.

- `config_retenciones` (0032): catálogo tributario editable por empresa (retefuente/ica/reteiva/inc) +
  la fila especial `tipo='uvt'` con el valor del UVT en pesos. Semilla vacía = opt-in (nada cambia).
- `retenciones_documento` (0033): renglón calculado por documento (venta/compra). La clave natural
  (doc_tipo, doc_id, tipo, concepto) hace idempotente reaplicar el motor sobre el mismo documento.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)
TARIFA = Numeric(9, 4)


class ConfigRetencion(TenantBase):
    """Una regla tributaria editable del tenant (retefuente/ica/reteiva/inc/uvt)."""

    __tablename__ = "config_retenciones"
    __table_args__ = (
        UniqueConstraint("tipo", "concepto", name="uq_config_retenciones_tipo_concepto"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)
    concepto: Mapped[str] = mapped_column(Text, nullable=False)
    base_minima_uvt: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tarifa: Mapped[Decimal] = mapped_column(TARIFA, nullable=False, default=Decimal("0"))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    editable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RetencionDocumento(TenantBase):
    """Renglón de retención/INC calculado y persistido para un documento (venta o compra)."""

    __tablename__ = "retenciones_documento"
    __table_args__ = (
        UniqueConstraint(
            "doc_tipo", "doc_id", "tipo", "concepto", name="uq_retenciones_documento_doc"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doc_tipo: Mapped[str] = mapped_column(Text, nullable=False)   # 'venta' | 'compra'
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tipo: Mapped[str] = mapped_column(Text, nullable=False)       # 'retefuente'|'ica'|'reteiva'|'inc'
    concepto: Mapped[str] = mapped_column(Text, nullable=False)
    base: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tarifa: Mapped[Decimal] = mapped_column(TARIFA, nullable=False)
    valor: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
