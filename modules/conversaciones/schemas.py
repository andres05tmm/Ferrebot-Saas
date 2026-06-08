"""Contratos Pydantic del pack de conversación / handoff (proyección de lectura)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


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
