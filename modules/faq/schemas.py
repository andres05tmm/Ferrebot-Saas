"""Contratos Pydantic del pack FAQ / conocimiento (crear/leer)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConocimientoCrear(BaseModel):
    """Entrada de conocimiento que nutre el negocio (alta/edición desde el dashboard)."""

    titulo: str = Field(min_length=1)
    contenido: str = Field(min_length=1)
    activo: bool = True
    orden: int = 0


class ConocimientoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    titulo: str
    contenido: str
    activo: bool
    orden: int
    creado_en: datetime
    actualizado_en: datetime | None
