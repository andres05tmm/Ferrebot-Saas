"""Schemas Pydantic del pack postventa (dashboard)."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PostventaConfigLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activo: bool
    horas_tras_evento: int
    seguir_citas: bool
    seguir_pedidos: bool
    google_maps_url: str | None
    calificacion_minima_resena: int


class PostventaConfigActualizar(BaseModel):
    activo: bool = True
    horas_tras_evento: int = Field(default=3, ge=1, le=48)
    seguir_citas: bool = True
    seguir_pedidos: bool = True
    google_maps_url: str | None = None
    calificacion_minima_resena: int = Field(default=4, ge=1, le=5)


class RespuestaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    telefono: str
    calificacion: int
    comentario: str | None
    creado_en: datetime
