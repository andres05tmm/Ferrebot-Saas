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
    # None = total; lista = parcial (solo líneas de catálogo; vacía no es una devolución válida).
    lineas: list[DevolucionLineaCrear] | None = Field(default=None, min_length=1)


class VentaFacturadaLeer(BaseModel):
    """Una venta con documento fiscal VIVO (POS o factura electrónica), candidata a nota crédito.

    Alimenta la lista del tab Devoluciones: solo ventas donde SÍ se emitió un documento DIAN (tipo
    'pos'/'factura', estado pendiente|aceptada). La nota crédito solo procede sobre una factura aceptada;
    `fiscal_estado` deja ver cuáles ya lo están. `cufe` es el CUFE/CUDE del documento (buscable)."""

    id: int
    consecutivo: int
    fecha: datetime
    total: Decimal
    metodo_pago: str
    fiscal_tipo: str            # 'pos' | 'factura'
    fiscal_estado: str          # 'pendiente' | 'aceptada'
    cufe: str | None = None
    fiscal_numero: int | None = None
    fiscal_prefijo: str | None = None


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
