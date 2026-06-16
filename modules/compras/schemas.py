"""Contratos Pydantic de compras (api-contract.md §compras)."""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProveedorRef(BaseModel):
    """Referencia a un proveedor: por `id` existente, o por `nombre` (+nit) para get-or-create."""

    id: int | None = None
    nombre: str | None = None
    nit: str | None = None

    @model_validator(mode="after")
    def _id_o_nombre(self) -> "ProveedorRef":
        if self.id is None and not (self.nombre and self.nombre.strip()):
            raise ValueError("El proveedor requiere `id` o `nombre`")
        return self


class CompraItemCrear(BaseModel):
    """Una línea de la compra: el producto, la cantidad recibida y su costo unitario."""

    producto_id: int
    cantidad: Decimal = Field(gt=0)
    costo: Decimal = Field(ge=0)


class CompraCrear(BaseModel):
    """Cuerpo del POST /compras: proveedor + items. El total lo calcula el servidor."""

    proveedor: ProveedorRef
    fecha: date | None = None
    items: list[CompraItemCrear] = Field(min_length=1)
    # Idempotencia (ai-tools.md §4): la fija el cliente/bot. En REST llega por el header
    # `Idempotency-Key` (el router la copia aquí). Misma key + mismo payload → la compra original;
    # misma key + payload distinto → idempotencia_conflicto.
    idempotency_key: str | None = None


class CompraLeer(BaseModel):
    """Vista de salida de una compra (cabecera con el nombre del proveedor y el total)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    proveedor_id: int | None
    proveedor_nombre: str | None
    fecha: datetime
    total: Decimal
