"""Contratos Pydantic de cuentas por pagar a proveedor (api-contract.md §proveedores)."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    # Vencimiento real impreso en la factura (pack_pagar). OPCIONAL y backward-compatible: si es NULL,
    # el motor de pagar lo deriva de `fecha + plazo_default_dias` (comportamiento actual sin cambios).
    fecha_vencimiento: date | None = None

    @model_validator(mode="after")
    def _vencimiento_no_anterior_a_fecha(self) -> "FacturaProveedorCrear":
        """Si se dan ambas fechas, el vencimiento no puede ser anterior a la fecha de la factura.

        Cuando `fecha` es None (se asume hoy en el servicio) no se compara: registrar una factura YA
        vencida es válido (la cuenta debe poder marcarse vencida), solo se prohíbe el orden absurdo.
        """
        if (
            self.fecha_vencimiento is not None
            and self.fecha is not None
            and self.fecha_vencimiento < self.fecha
        ):
            raise ValueError("La fecha de vencimiento no puede ser anterior a la fecha de la factura")
        return self


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
    fecha_vencimiento: date | None
    foto_url: str | None
    foto_nombre: str | None


class ResumenCxP(BaseModel):
    """Resumen de cuentas por pagar: total adeudado y nº de facturas pendientes."""

    total_adeudado: Decimal
    facturas_pendientes: int
