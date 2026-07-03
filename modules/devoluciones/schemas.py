"""Contratos Pydantic de devoluciones (entrada validada, salida del API)."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class DevolucionLineaCrear(BaseModel):
    """Una línea a devolver: producto de catálogo + cantidad (≤ lo vendido en esa venta)."""

    producto_id: int
    cantidad: Decimal = Field(gt=0)


class DevolucionCrear(BaseModel):
    """Solicitud de devolución. `lineas=None` → devolución TOTAL (todo lo vendido en la venta)."""

    venta_id: int
    motivo: str | None = None
    idempotency_key: str | None = None
    # None = total; lista = parcial (solo líneas de catálogo).
    lineas: list[DevolucionLineaCrear] | None = None


class DevolucionDetalleLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    producto_id: int | None
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    total_linea: Decimal


class DevolucionLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    venta_id: int
    nota_id: int | None
    total: Decimal
    metodo_reintegro: str
    motivo: str | None
    estado: str
    creado_en: datetime | None = None
