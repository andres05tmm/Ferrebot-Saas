"""Modelo Cliente (schema.md / tenant 0001). Solo columnas que existen en la migración 0001.

`saldo_fiado` es un contador denormalizado del saldo de crédito; la fuente de verdad es
`fiados_movimientos` (se actualiza en la misma transacción que el movimiento).
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase


class Cliente(TenantBase):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    tipo_documento: Mapped[str | None] = mapped_column(Text)
    documento: Mapped[str | None] = mapped_column(Text)
    telefono: Mapped[str | None] = mapped_column(Text)
    correo: Mapped[str | None] = mapped_column(Text)
    direccion: Mapped[str | None] = mapped_column(Text)
    ciudad_dane: Mapped[str | None] = mapped_column(Text)
    regimen: Mapped[str | None] = mapped_column(Text)
    saldo_fiado: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
