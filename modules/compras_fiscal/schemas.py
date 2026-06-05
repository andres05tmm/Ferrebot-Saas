"""Contratos Pydantic de compras fiscales (api-contract.md §compras-fiscal).

`CompraFiscalCrear` valida que los montos sean `>= 0` y que `base + iva == total` tolerando el redondeo
de centavos (la DIAN calcula el IVA por línea y al sumar puede quedar a ±1 centavo del total). Una
incoherencia mayor a un centavo es un error de captura → 422.
"""
from datetime import datetime
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
