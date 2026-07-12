"""Contratos Pydantic de cuentas por pagar a proveedor (api-contract.md §proveedores)."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.config.timezone import today_co


def _no_futura(v: date | None) -> date | None:
    """Una factura o un abono se registran cuando ya ocurrieron: fecha futura = typo que descuadra
    los reportes del periodo. (El vencimiento sí puede ser futuro; este guard no le aplica.)"""
    if v is not None and v > today_co():
        raise ValueError("La fecha no puede ser futura")
    return v


class ProveedorLeer(BaseModel):
    """Proveedor registrado para los desplegables (modal de producto): id/nombre/nit + mini-CRM.

    El vertical construcción (spec 10 / tenant 0046) suma `tipo` (planta de asfalto, cantera…) y datos
    de `contacto_*`, para el análisis de precios por rubro. Se exponen OPCIONALES (default None) →
    backward-compatible: un proveedor del POS sin estos datos los devuelve como null. `tipo` se lee como
    `str | None` (el valor viene de la BD, ya válido contra el enum `tipo_proveedor`).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    nit: str | None

    # --- Mini-CRM construcción (spec 10 / tenant 0046). OPCIONALES, backward-compatible. ---
    tipo: str | None = None
    contacto_nombre: str | None = None
    contacto_telefono: str | None = None
    contacto_email: str | None = None


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

    _fecha_no_futura = field_validator("fecha")(_no_futura)

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

    _fecha_no_futura = field_validator("fecha")(_no_futura)


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
