"""Contratos Pydantic del pack de conversación / handoff (proyección de lectura + inbox)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversacionLeer(BaseModel):
    """Proyección de lectura de una conversación (para la bandeja del dashboard)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_telefono: str
    estado: str
    motivo: str | None
    creada_en: datetime
    escalada_en: datetime | None
    resuelta_en: datetime | None


class MensajeLeer(BaseModel):
    """Un mensaje del hilo (entrante/saliente · cliente/bot/asesor) para el inbox."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_telefono: str
    direccion: str
    autor: str
    texto: str
    creada_en: datetime


class ConversacionInbox(ConversacionLeer):
    """Fila del inbox: la conversación + su último mensaje (texto y cuándo) para la lista izquierda."""

    ultimo_texto: str | None = None
    ultimo_autor: str | None = None
    ultimo_en: datetime | None = None


class ResponderEntrada(BaseModel):
    """Cuerpo de POST /conversaciones/{id}/responder: el texto que el asesor envía al cliente."""

    texto: str = Field(min_length=1, max_length=4096)
