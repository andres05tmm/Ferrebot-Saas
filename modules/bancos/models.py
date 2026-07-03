"""Modelo ORM de transferencias Bancolombia (schema.md / tenant 0001), mapeado por ADR 0025.

Tabla de negocio sin `empresa_id`: la base ES la frontera del tenant. Registra las transferencias
entrantes parseadas de las notificaciones (Gmail): `gmail_message_id` UNIQUE da la idempotencia de
ingesta (no re-registrar el mismo correo). Existía en la migración 0001 sin modelo ORM.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Numeric, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)


class BancolombiaTransferencia(TenantBase):
    __tablename__ = "bancolombia_transferencias"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    gmail_message_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    hora: Mapped[str | None] = mapped_column(Text)
    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remitente: Mapped[str | None] = mapped_column(Text)
    descripcion: Mapped[str | None] = mapped_column(Text)
    tipo_transaccion: Mapped[str | None] = mapped_column(Text)
    referencia: Mapped[str | None] = mapped_column(Text)
    notificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
