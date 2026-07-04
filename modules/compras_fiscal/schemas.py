"""Contratos Pydantic de compras fiscales (api-contract.md §compras-fiscal).

`CompraFiscalCrear` valida que los montos sean `>= 0` y que `base + iva == total` tolerando el redondeo
de centavos (la DIAN calcula el IVA por línea y al sumar puede quedar a ±1 centavo del total). Una
incoherencia mayor a un centavo es un error de captura → 422.
"""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Holgura admitida entre base+iva y total: el redondeo por línea de la DIAN puede desviar ±1 centavo.
TOLERANCIA_CENTAVO = Decimal("0.01")


class CompraFiscalCrear(BaseModel):
    """Cuerpo del POST /compras-fiscal: desglose de IVA de una compra fiscal."""

    proveedor_nit: str
    base: Decimal = Field(ge=0)
    iva: Decimal = Field(ge=0)
    total: Decimal = Field(ge=0)
    soporte_url: str | None = None
    compra_id: int | None = None

    @model_validator(mode="after")
    def _coherencia_montos(self) -> "CompraFiscalCrear":
        """base + iva debe igualar total (tolera ±1 centavo por el redondeo de la DIAN)."""
        if abs((self.base + self.iva) - self.total) > TOLERANCIA_CENTAVO:
            raise ValueError(
                f"Incoherencia: base ({self.base}) + iva ({self.iva}) != total ({self.total})"
            )
        return self


class CompraFiscalLeer(BaseModel):
    """Vista de salida de una compra fiscal (incluye el estado de los eventos RADIAN del Slice 6b)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    compra_id: int | None
    proveedor_nit: str | None
    base: Decimal
    iva: Decimal
    total: Decimal
    soporte_url: str | None
    creado_en: datetime
    # RADIAN-FE (Slice 6b): NULL mientras no se hayan enviado eventos DIAN sobre la factura.
    cufe_proveedor: str | None = None
    evento_030_at: datetime | None = None
    evento_031_at: datetime | None = None
    evento_032_at: datetime | None = None
    evento_033_at: datetime | None = None
    evento_estado: str | None = None
    evento_error: str | None = None


class ImportarCufe(BaseModel):
    """Cuerpo del POST /compras-fiscal/{id}/importar: el CUFE de la factura recibida (capturado a mano)."""

    cufe: str = Field(min_length=1)


class ReclamarMotivo(BaseModel):
    """Cuerpo del POST /compras-fiscal/{id}/reclamar: motivo opcional del reclamo (evento 031)."""

    motivo: str | None = None


class AmbienteFiscal(BaseModel):
    """Ambiente DIAN declarado de la empresa, para la confirmación del operador en la UI."""

    ambiente: str


class EscanearQR(BaseModel):
    """Cuerpo del POST /facturas-recibidas/escanear (ADR 0020, F1): el QR + los datos de cabecera.

    `qr` es el TEXTO leído del QR (URL DIAN, campos `CUFE:` o el hash crudo); de ahí sale el CUFE. Los
    datos de cabecera (proveedor, montos, vencimiento) los aporta el operador: la lectura del documento
    oficial de MATIAS es Pregunta abierta #1 del ADR (sin confirmar), así que v1 registra la deuda con el
    monto/vencimiento capturados + acuse RADIAN + CUFE archivado. `base`/`iva` son opcionales (0 = desglose
    desconocido, como la fiscal derivada de una compra); si se dan ambos, deben cuadrar con `total`.
    """

    qr: str = Field(min_length=1)
    proveedor_nit: str = Field(min_length=1)
    proveedor_nombre: str | None = None
    numero_factura: str | None = None
    descripcion: str | None = None
    base: Decimal = Field(default=Decimal("0"), ge=0)
    iva: Decimal = Field(default=Decimal("0"), ge=0)
    total: Decimal = Field(gt=0)
    fecha: date | None = None
    fecha_vencimiento: date | None = None

    @model_validator(mode="after")
    def _coherencia(self) -> "EscanearQR":
        """Si se declaran base e IVA (>0), base+iva debe igualar total (±1 centavo). Vencimiento ≥ fecha."""
        if (self.base + self.iva) > 0 and abs((self.base + self.iva) - self.total) > TOLERANCIA_CENTAVO:
            raise ValueError(
                f"Incoherencia: base ({self.base}) + iva ({self.iva}) != total ({self.total})"
            )
        if (
            self.fecha_vencimiento is not None
            and self.fecha is not None
            and self.fecha_vencimiento < self.fecha
        ):
            raise ValueError("La fecha de vencimiento no puede ser anterior a la fecha de la factura")
        return self


class FacturaRecibidaLeer(BaseModel):
    """Vista de una factura de proveedor RECIBIDA por QR: soporte fiscal (CUFE + RADIAN) + cuenta por pagar.

    Compone la fila `compras_fiscal` (CUFE, montos, estado de los eventos DIAN) con la
    `facturas_proveedores` (la deuda con su vencimiento y saldo). El `cuenta_por_pagar_id` ES el CUFE:
    para las recibidas por QR, el CUFE es el identificador único de la deuda (dedup PK + índice UNIQUE).
    """

    cufe: str
    fiscal_id: int
    proveedor_nit: str | None
    base: Decimal
    iva: Decimal
    total: Decimal
    # Estado RADIAN (NULL si MATIAS no estaba configurado: degradó a solo registrar deuda + soporte).
    evento_030_at: datetime | None = None
    evento_estado: str | None = None
    evento_error: str | None = None
    # Cuenta por pagar (facturas_proveedores) enlazada. Ausente solo si la deuda no pudo crearse.
    cuenta_por_pagar_id: str | None = None
    fecha: date | None = None
    fecha_vencimiento: date | None = None
    pendiente: Decimal | None = None
    estado: str | None = None
    descripcion: str | None = None
