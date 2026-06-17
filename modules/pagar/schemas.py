"""Schemas Pydantic del pack pagar (config + cuentas por pagar). Validación de toda entrada.

Los límites de `PagarConfigActualizar` son el guardarraíl que evita configs absurdas (avisar cada 0
días, plazos negativos): se validan AL SETEAR, no se confía en la BD.
"""
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PagarConfigLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activo: bool
    dias_aviso_previo: int
    cadencia_dias: int
    hora_inicio: time
    hora_fin: time
    plazo_default_dias: int


class PagarConfigActualizar(BaseModel):
    activo: bool = True
    dias_aviso_previo: int = Field(default=3, ge=0, le=60)
    cadencia_dias: int = Field(default=3, ge=1, le=60)
    hora_inicio: time = time(8, 0)
    hora_fin: time = time(18, 0)
    plazo_default_dias: int = Field(default=30, ge=1, le=365)


class CuentaPorPagarLeer(BaseModel):
    """Fila de la página de cuentas por pagar: la factura + su vencimiento efectivo y estado."""

    factura_id: str
    proveedor: str
    pendiente: Decimal
    fecha: date
    vencimiento_efectivo: date
    dias_para_vencer: int
    por_vencer: bool
    vencida: bool
    avisos_enviados: int = 0
    ultimo_aviso_en: datetime | None = None
