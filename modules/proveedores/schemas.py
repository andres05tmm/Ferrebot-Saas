"""Contratos Pydantic de cuentas por pagar a proveedor (api-contract.md §proveedores)."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProveedorLeer(BaseModel):
    """Proveedor registrado para los desplegables (modal de producto). Solo id/nombre/nit."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    nit: str | None


class FacturaProveedorCrear(BaseModel):
    """Alta de una factura de proveedor (deuda). `id` = nº de factura del proveedor."""

    id: str = Field(min_length=1)
    proveedor: str = Field(min_length=1)
    descripcion: str | None = None
    total: Decimal = Field(gt=0)
    fecha: date | None = None   # default hoy Colombia en el servicio


class AbonoCrear(BaseModel):
    """Registro de un abono a una factura de proveedor."""

    factura_id: str = Field(min_length=1)
    monto: Decimal = Field(gt=0)
    fecha: date | None = None   # default hoy Colombia en el servicio


class FacturaProveedorLeer(BaseModel):
    """Vista de salida de una factura de proveedor con su saldo derivado."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    proveedor: str
    descripcion: str | None
    total: Decimal
    pagado: Decimal
    pendiente: Decimal
    estado: str
    fecha: date
    foto_url: str | None
    foto_nombre: str | None


class ResumenCxP(BaseModel):
    """Resumen de cuentas por pagar: total adeudado y nº de facturas pendientes."""

    total_adeudado: Decimal
    facturas_pendientes: int
