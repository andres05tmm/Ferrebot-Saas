"""Contratos Pydantic del catálogo e inventario (api-contract.md §productos/inventario)."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str | None
    nombre: str
    categoria: str | None
    marca: str | None
    unidad_medida: str
    precio_venta: Decimal
    precio_mayorista: Decimal | None
    precio_umbral: Decimal | None
    precio_bajo_umbral: Decimal | None
    precio_sobre_umbral: Decimal | None
    iva: int
    permite_fraccion: bool
    activo: bool


class PrecioLeer(BaseModel):
    producto_id: int
    cantidad: Decimal
    precio_unitario: Decimal
    total: Decimal
    regla: str  # escalonado | fraccion | simple


class StockLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    producto_id: int
    nombre: str
    stock_actual: Decimal
    stock_minimo: Decimal
    bajo: bool


class AjusteCrear(BaseModel):
    producto_id: int
    # Delta con signo: +5 (sobrante encontrado) / -3 (merma). El tipo de movimiento es AJUSTE.
    cantidad: Decimal = Field(description="Delta a aplicar al stock; positivo o negativo, distinto de 0")
    motivo: str = Field(min_length=1)

    @field_validator("cantidad")
    @classmethod
    def _no_cero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("El delta del ajuste no puede ser 0")
        return v


class AjusteLeer(BaseModel):
    producto_id: int
    delta: Decimal
    stock_actual: Decimal
    replay: bool


class KardexItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo: str
    cantidad: Decimal
    costo_unitario: Decimal | None
    referencia: str | None
    usuario_id: int | None
    creado_en: datetime
