"""Modelos ORM de la memoria del bot (mapean tablas YA creadas en tenant/0001; no las recrean).

Solo columnas que existen en la migración 0001 (+ el UNIQUE(tipo, clave) que agrega la 0004 sobre
`memoria_entidades`). Sin `empresa_id`: el aislamiento lo da la base del tenant (multitenancy.md #4).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase


class ConversacionBot(TenantBase):
    __tablename__ = "conversaciones_bot"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    rol: Mapped[str | None] = mapped_column(Text)
    contenido: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MemoriaEntidad(TenantBase):
    __tablename__ = "memoria_entidades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tipo: Mapped[str | None] = mapped_column(Text)
    clave: Mapped[str | None] = mapped_column(Text)
    valor: Mapped[dict | None] = mapped_column(JSONB)
    actualizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiCostoDiario(TenantBase):
    __tablename__ = "api_costo_diario"

    fecha: Mapped[date] = mapped_column(Date, primary_key=True)
    modelo: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(BigInteger)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger)
    costo: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
