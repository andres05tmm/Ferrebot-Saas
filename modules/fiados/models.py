"""Modelos de fiados y su ledger de movimientos (schema.md / tenant 0001 + 0003)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)

fiado_mov_tipo = PgEnum("cargo", "abono", name="fiado_mov_tipo", create_type=False)


class Fiado(TenantBase):
    __tablename__ = "fiados"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    venta_id: Mapped[int | None] = mapped_column(BigInteger)
    monto: Mapped[Decimal | None] = mapped_column(MONEY)
    saldo: Mapped[Decimal | None] = mapped_column(MONEY)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FiadoMovimiento(TenantBase):
    __tablename__ = "fiados_movimientos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    fiado_id: Mapped[int] = mapped_column(BigInteger)
    tipo: Mapped[str] = mapped_column(fiado_mov_tipo, nullable=False)
    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
