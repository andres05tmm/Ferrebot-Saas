"""Modelos ORM del soporte tributario de reportes (schema.md / tenant 0001).

Tablas de negocio sin `empresa_id`: la base ES la frontera del tenant. Mapean tablas que existían en
la migración 0001 sin modelo ORM (ADR 0025). El Libro IVA y los saldos bimestrales son el soporte del
IVA a pagar/descontar; hoy el repositorio de reportes AGREGA en vivo desde ventas/compras, y estas
tablas son la materialización persistente (libro append-only + saldo por periodo).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)


class IvaSaldoBimestral(TenantBase):
    """Saldo de IVA por bimestre: generado (ventas) vs descontable (compras) → saldo a pagar/favor."""

    __tablename__ = "iva_saldos_bimestrales"
    __table_args__ = (UniqueConstraint("anio", "bimestre", name="uq_iva_saldos_periodo"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    anio: Mapped[int | None] = mapped_column(Integer)
    bimestre: Mapped[int | None] = mapped_column(SmallInteger)
    iva_generado: Mapped[Decimal | None] = mapped_column(MONEY)
    iva_descontable: Mapped[Decimal | None] = mapped_column(MONEY)
    saldo: Mapped[Decimal | None] = mapped_column(MONEY)


class LibroIVA(TenantBase):
    """Libro IVA: renglón por operación con base e IVA (soporte tributario append-only)."""

    __tablename__ = "libro_iva"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fecha: Mapped[date | None] = mapped_column(Date)
    tipo: Mapped[str | None] = mapped_column(Text)
    base: Mapped[Decimal | None] = mapped_column(MONEY)
    iva: Mapped[Decimal | None] = mapped_column(MONEY)
    referencia: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
