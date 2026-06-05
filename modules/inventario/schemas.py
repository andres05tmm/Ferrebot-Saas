"""Contratos Pydantic del catálogo e inventario (api-contract.md §productos/inventario)."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FraccionCrear(BaseModel):
    """Una fila de productos_fracciones al crear/editar (p. ej. 1/2 con su precio total)."""

    fraccion: str = Field(min_length=1, description="Etiqueta de la fracción, p. ej. '1/2'")
    decimal: Decimal | None = Field(default=None, ge=0, description="Equivalente decimal (0.5 = 1/2)")
    precio_total: Decimal = Field(ge=0, description="Precio total de la fracción")
    precio_unitario: Decimal | None = Field(default=None, ge=0)


class _ProductoBase(BaseModel):
    """Campos comunes a crear y actualizar (el contrato editable del producto)."""

    nombre: str = Field(min_length=1)
    codigo: str | None = None
    categoria: str | None = None
    marca: str | None = None
    unidad_medida: str = Field(min_length=1, default="unidad")
    precio_venta: Decimal = Field(ge=0)
    precio_compra: Decimal | None = Field(default=None, ge=0)
    precio_mayorista: Decimal | None = Field(default=None, ge=0)
    # Precio escalonado por cantidad: los tres NULL o los tres presentes (lo valida el motor de precios).
    precio_umbral: Decimal | None = Field(default=None, ge=0)
    precio_bajo_umbral: Decimal | None = Field(default=None, ge=0)
    precio_sobre_umbral: Decimal | None = Field(default=None, ge=0)
    iva: int = Field(default=19, ge=0, le=100)
    permite_fraccion: bool = False
    activo: bool = True
    fracciones: list[FraccionCrear] = Field(default_factory=list)
    stock_minimo: Decimal = Field(default=Decimal("0"), ge=0)

    @field_validator("codigo", "categoria", "marca", mode="before")
    @classmethod
    def _vacio_a_none(cls, v: str | None) -> str | None:
        """Normaliza cadenas vacías/espacios a None (el código vacío no debe colisionar como UNIQUE)."""
        if v is None:
            return None
        v = v.strip()
        return v or None


class ProductoCrear(_ProductoBase):
    """Alta de producto. `stock_inicial > 0` genera una ENTRADA de inventario (regla #7)."""

    stock_inicial: Decimal = Field(default=Decimal("0"), ge=0)


class ProductoActualizar(_ProductoBase):
    """Edición de producto. No toca `stock_actual` (eso va por /inventario/ajuste); reemplaza fracciones."""


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
    movimiento_id: int
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
