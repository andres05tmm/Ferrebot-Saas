"""Contratos Pydantic de cuentas por pagar a proveedor (api-contract.md §proveedores)."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.config.timezone import today_co
from modules.caja.schemas import OrigenFondos
from modules.proveedores.pagos import PartePago


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
    # A quién se le debe (0070). Opcional: si no viene, el servicio lo resuelve por nombre.
    proveedor_id: int | None = Field(default=None, gt=0)
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


class AsignarProveedor(BaseModel):
    """Asigna una factura huérfana (nombre que no casó en el backfill) a su proveedor real."""

    proveedor_id: int = Field(gt=0)


class AbonoCrear(BaseModel):
    """Registro de un abono a una factura de proveedor.

    `origen_fondos` dice de dónde sale la plata: `caja` postea el egreso en el cajón del día (exige
    caja abierta); `efectivo_externo` (efectivo guardado de días anteriores) y `banco` bajan la deuda
    y dejan registrado el medio, sin tocar el arqueo.
    """

    factura_id: str = Field(min_length=1)
    monto: Decimal = Field(gt=0)
    fecha: date | None = None   # default hoy Colombia en el servicio
    origen_fondos: OrigenFondos = "caja"
    # Pago MIXTO (0068): parte en efectivo del cajón, parte por transferencia. Vacío = todo por
    # `origen_fondos`; con partes, deben sumar el monto del abono.
    pagos: list[PartePago] = Field(default_factory=list)

    _fecha_no_futura = field_validator("fecha")(_no_futura)


class FacturaProveedorLeer(BaseModel):
    """Vista de salida de una factura de proveedor con su saldo derivado."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    proveedor: str
    proveedor_id: int | None = None
    descripcion: str | None
    total: Decimal
    pagado: Decimal
    pendiente: Decimal
    estado: str
    fecha: date
    fecha_vencimiento: date | None
    foto_url: str | None
    foto_nombre: str | None


class ProveedorEstado(BaseModel):
    """Cómo va la relación con un proveedor, para la lista del tab (una fila por proveedor).

    Junta lo que hoy vivía en tres pantallas distintas: la deuda (cuentas por pagar), el ritmo de
    entrega (pedidos) y la última compra.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    nit: str | None = None
    telefono: str | None = None
    contacto_nombre: str | None = None
    contacto_telefono: str | None = None
    saldo_pendiente: Decimal = Decimal("0")
    vencido: Decimal = Decimal("0")
    facturas_pendientes: int = 0
    pedidos_en_camino: int = 0
    lead_time_promedio_horas: float | None = None
    ultima_compra: date | None = None


class MovimientoCuenta(BaseModel):
    """Una línea del estado de cuenta: lo que se le debió (factura) o lo que se le pagó (abono),
    con el saldo corrido — el "detailed ledger" que piden los contadores."""

    fecha: date
    tipo: str                      # 'factura' | 'abono'
    referencia: str                # nº de factura
    descripcion: str | None = None
    cargo: Decimal = Decimal("0")  # lo que aumentó la deuda
    abono: Decimal = Decimal("0")  # lo que la bajó
    saldo: Decimal = Decimal("0")  # saldo corrido tras el movimiento
    medio: str | None = None       # de dónde salió la plata del abono


class EstadoCuentaProveedor(BaseModel):
    """Ficha de deuda de un proveedor: saldo, antigüedad y el movimiento a movimiento."""

    proveedor_id: int
    proveedor_nombre: str
    desde: date
    hasta: date
    saldo_pendiente: Decimal
    vencido: Decimal
    saldo_anterior: Decimal        # lo que ya se debía antes del rango (arranque del corrido)
    aging: dict[str, Decimal] = {}
    movimientos: list[MovimientoCuenta] = []


class ResumenCxP(BaseModel):
    """Resumen de cuentas por pagar: total adeudado y nº de facturas pendientes."""

    total_adeudado: Decimal
    facturas_pendientes: int
