"""Contratos Pydantic de herramientas (spec cliente 06_HERRAMIENTAS — tenant 0043).

Campos = nombres de columna del ORM (`modules.herramientas.models`), como fija el contrato de la fase.
CRUD ligero: activos menores con `cantidad` y una `ubicacion_actual` de texto libre. Edición PARCIAL
(PATCH): todos los campos opcionales, solo se aplican los enviados (ver `service.actualizar`).
"""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Literales EXACTOS al enum `estado_herramienta` (migración 0043).
EstadoHerramienta = Literal["DISPONIBLE", "EN_OBRA", "MANTENIMIENTO", "PERDIDA", "BAJA"]


class HerramientaCrear(BaseModel):
    """Alta de una herramienta. `codigo`/`nombre` son NOT NULL en la spec."""

    codigo: str = Field(min_length=1)
    nombre: str = Field(min_length=1)
    categoria: str | None = None
    cantidad: int = Field(default=1, ge=0)
    ubicacion_actual: str | None = None   # obra o bodega
    estado: EstadoHerramienta = "DISPONIBLE"
    valor_reposicion: Decimal | None = Field(default=None, ge=0)
    notas: str | None = None


class HerramientaActualizar(BaseModel):
    """Edición PARCIAL (PATCH): solo los campos presentes en el cuerpo se aplican (`exclude_unset`)."""

    codigo: str | None = Field(default=None, min_length=1)
    nombre: str | None = Field(default=None, min_length=1)
    categoria: str | None = None
    cantidad: int | None = Field(default=None, ge=0)
    ubicacion_actual: str | None = None
    estado: EstadoHerramienta | None = None
    valor_reposicion: Decimal | None = Field(default=None, ge=0)
    notas: str | None = None


class HerramientaLeer(BaseModel):
    """Vista de salida de una herramienta (todas las columnas del ORM, soft delete incluido)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    nombre: str
    categoria: str | None
    cantidad: int
    ubicacion_actual: str | None
    estado: str
    valor_reposicion: Decimal | None
    notas: str | None
    creado_en: datetime
    actualizado_en: datetime
    eliminado_en: datetime | None
