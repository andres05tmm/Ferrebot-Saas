"""Modelo del pack transversal de conversación / handoff (fuente: migración 0009_conversaciones).

`Conversacion` lleva el estado de la conversación de un cliente de cara al público: `bot` (el agente
atiende) o `humano` (escalada; el runtime se pausa hasta que el negocio la resuelva). Una fila por
`cliente_telefono` (su número = identidad). Vive en la base del propio tenant (sin `empresa_id`).
Fechas en TIMESTAMPTZ (se operan en hora Colombia, `COLOMBIA_TZ`, regla no negociable #4).
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

# El tipo lo crea la migración (create_type=False): aquí solo se mapea.
conversacion_estado = PgEnum("bot", "humano", name="conversacion_estado", create_type=False)


class Conversacion(TenantBase):
    """Estado de la conversación de un cliente: `bot` | `humano`. Una fila por `cliente_telefono`."""

    __tablename__ = "conversaciones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_telefono: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    estado: Mapped[str] = mapped_column(conversacion_estado, nullable=False, server_default="bot")
    motivo: Mapped[str | None] = mapped_column(Text)  # por qué se escaló (lo da el agente)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    escalada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resuelta_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
