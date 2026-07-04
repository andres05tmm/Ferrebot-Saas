"""Modelos del motor contable (ADR 0030). Espejan el DDL de las migraciones 0037-0041.

Asientos INMUTABLES append-only: un `journal_entry` `posted` no se edita — se corrige con un
asiento espejo (`reverso_de`). La frontera del tenant es la base (sin `empresa_id`, regla #4 de
multitenancy). Los enums (naturaleza, direction, estado, período) son TEXT + CHECK en la base —no
enums PG— siguiendo el criterio de `devoluciones`/`retenciones`; el ORM solo mapea.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

MONEY = Numeric(16, 2)   # saldos acumulados pueden exceder NUMERIC(12,2) de una línea suelta


class PucCuenta(TenantBase):
    """Cuenta del Plan Único de Cuentas (árbol). Solo las hojas (`imputable`) reciben movimientos."""

    __tablename__ = "puc_cuentas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    codigo: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("puc_cuentas.id"))
    naturaleza: Mapped[str] = mapped_column(Text, nullable=False)   # 'debito' | 'credito'
    imputable: Mapped[bool] = mapped_column(nullable=False, default=False)
    activo: Mapped[bool] = mapped_column(nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PeriodoContable(TenantBase):
    """Período mensual con candado: `open` acepta postings; `locked`/`closed` los rechazan."""

    __tablename__ = "periodo_contable"
    __table_args__ = (UniqueConstraint("anio", "mes", name="uq_periodo_contable_anio_mes"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    anio: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    mes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, default="open")  # open|locked|closed
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class JournalEntry(TenantBase):
    """Asiento (cabecera). Inmutable una vez `posted`; se corrige con un espejo (`reverso_de`)."""

    __tablename__ = "journal_entry"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    periodo_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("periodo_contable.id"))
    estado: Mapped[str] = mapped_column(Text, nullable=False, default="pending")  # pending|posted
    origen_tipo: Mapped[str] = mapped_column(Text, nullable=False)
    origen_id: Mapped[int | None] = mapped_column(BigInteger)
    descripcion: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    reverso_de: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("journal_entry.id"))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    posted_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lineas: Mapped[list["JournalLine"]] = relationship(
        back_populates="entry", cascade="all, delete-orphan", lazy="selectin", order_by="JournalLine.orden",
    )


class JournalLine(TenantBase):
    """Línea de asiento: `direction` debit/credit con `amount` sin signo (> 0)."""

    __tablename__ = "journal_line"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("journal_entry.id", ondelete="CASCADE"), nullable=False
    )
    cuenta_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("puc_cuentas.id"), nullable=False)
    direction: Mapped[str] = mapped_column(Text, nullable=False)   # 'debit' | 'credit'
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    orden: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    entry: Mapped["JournalEntry"] = relationship(back_populates="lineas")


class SaldoCache(TenantBase):
    """Saldo por cuenta/período, recomputable desde las líneas (patrón Square Books)."""

    __tablename__ = "saldo_cache"
    __table_args__ = (
        UniqueConstraint("cuenta_id", "periodo_id", name="uq_saldo_cache_cuenta_periodo"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cuenta_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("puc_cuentas.id"), nullable=False)
    periodo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("periodo_contable.id"), nullable=False
    )
    debitos: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    creditos: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    saldo: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
