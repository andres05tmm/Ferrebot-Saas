"""Contratos Pydantic de ventas (entrada validada, salida del API)."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MetodoPago = Literal[
    "efectivo", "transferencia", "tarjeta", "nequi", "daviplata", "fiado", "datafono"
]
Origen = Literal["web", "bot", "voz", "offline"]


class VentaDetalleCrear(BaseModel):
    producto_id: int | None = None
    descripcion: str | None = None
    cantidad: Decimal = Field(gt=0)
    # Catálogo: opcional (override de precio declarado). Varia: obligatorio.
    precio_unitario: Decimal | None = Field(default=None, ge=0)
    iva: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _validar_linea(self) -> "VentaDetalleCrear":
        if self.producto_id is None and (self.precio_unitario is None or not self.descripcion):
            raise ValueError("Una venta varia (sin producto_id) requiere descripcion y precio_unitario")
        return self


class VentaCrear(BaseModel):
    metodo_pago: MetodoPago
    cliente_id: int | None = None
    origen: Origen = "web"
    idempotency_key: str | None = None
    lineas: list[VentaDetalleCrear] = Field(min_length=1)


class VentaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    consecutivo: int
    cliente_id: int | None
    vendedor_id: int
    fecha: datetime
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    metodo_pago: str
    estado: str
    origen: str
    idempotency_key: str | None


class VentaDetalleLeer(BaseModel):
    """Línea de una venta (detalle). Solo lectura, para el detalle del historial."""

    model_config = ConfigDict(from_attributes=True)

    producto_id: int | None
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    iva: int


class VentaConLineas(VentaLeer):
    """Detalle de venta: cabecera (VentaLeer) + sus líneas. La LISTA usa VentaLeer (sin líneas)."""

    lineas: list[VentaDetalleLeer]
