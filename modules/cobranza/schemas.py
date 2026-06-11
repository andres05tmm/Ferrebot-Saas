"""Schemas Pydantic del pack cobranza (dashboard + motor). Validación de toda entrada (security.md)."""
from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CobranzaConfigLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activo: bool
    cadencia_dias: int
    max_recordatorios: int
    hora_inicio: time
    hora_fin: time
    saldo_minimo: Decimal


class CobranzaConfigActualizar(BaseModel):
    activo: bool = True
    cadencia_dias: int = Field(default=7, ge=1, le=60)
    max_recordatorios: int = Field(default=3, ge=1, le=10)
    hora_inicio: time = time(9, 0)
    hora_fin: time = time(19, 0)
    saldo_minimo: Decimal = Field(default=Decimal("0"), ge=0)


class PromesaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_id: int
    telefono: str
    fecha_promesa: date
    estado: str
    creado_en: datetime


class PagoReportadoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_id: int
    telefono: str
    nota: str | None
    verificado: bool
    creado_en: datetime


class DeudorLeer(BaseModel):
    """Fila de la página Cartera: el deudor + su estado de cobranza + su promesa vigente."""

    cliente_id: int
    nombre: str
    telefono: str | None
    saldo: Decimal
    opt_out: bool = False
    recordatorios_enviados: int = 0
    ultimo_recordatorio_en: datetime | None = None
    promesa_fecha: date | None = None


class OptOutActualizar(BaseModel):
    opt_out: bool
