"""Modelos del pack transversal de conversación / handoff (fuente: migraciones 0009 y 0024).

`Conversacion` lleva el ESTADO de la conversación de un cliente de cara al público: `bot` (el agente
atiende) o `humano` (escalada; el runtime se pausa hasta que el negocio la resuelva). Una fila por
`cliente_telefono` (su número = identidad).

`ConversacionMensaje` (0024) es el HILO: una fila por mensaje (entrante del cliente, respuesta del bot
o del asesor), la fuente del hilo visible en el inbox del dashboard. `cliente_telefono` referencia
lógicamente a `conversaciones.cliente_telefono` (sin FK forzada: el 1er mensaje precede a la fila de
estado). Ambos viven en la base del propio tenant (sin `empresa_id`). Fechas en TIMESTAMPTZ (se operan
en hora Colombia, `COLOMBIA_TZ`, regla no negociable #4).
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

# Los tipos los crea la migración (create_type=False): aquí solo se mapean.
conversacion_estado = PgEnum("bot", "humano", name="conversacion_estado", create_type=False)
mensaje_direccion = PgEnum("entrante", "saliente", name="mensaje_direccion", create_type=False)
mensaje_autor = PgEnum("cliente", "bot", "asesor", name="mensaje_autor", create_type=False)


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


class ConversacionMensaje(TenantBase):
    """Un mensaje del hilo de un cliente: `direccion` (entrante|saliente) + `autor` (cliente|bot|asesor)."""

    __tablename__ = "conversacion_mensajes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_telefono: Mapped[str] = mapped_column(Text, nullable=False)  # FK lógica a conversaciones
    direccion: Mapped[str] = mapped_column(mensaje_direccion, nullable=False)
    autor: Mapped[str] = mapped_column(mensaje_autor, nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
