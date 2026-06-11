"""Schemas Pydantic del pack ventas/cotizaciones (dashboard + motor)."""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class VentasWaConfigLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mostrar_stock: bool
    vigencia_dias: int


class VentasWaConfigActualizar(BaseModel):
    mostrar_stock: bool = True
    vigencia_dias: int = Field(default=3, ge=1, le=30)


class CotizacionItemLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    producto_id: int | None
    nombre: str
    cantidad: Decimal
    precio_unitario: Decimal
    subtotal: Decimal


class CotizacionLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_telefono: str
    cliente_nombre: str | None
    estado: str
    total: Decimal
    vigencia_hasta: date | None
    creado_en: datetime
    actualizado_en: datetime
    items: list[CotizacionItemLeer]


class MarcarCotizacion(BaseModel):
    estado: str = Field(pattern="^(aceptada|cancelada)$")
