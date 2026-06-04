"""Contratos Pydantic de fiados (api-contract.md §fiados)."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class FiadoCrear(BaseModel):
    cliente_id: int
    venta_id: int | None = None
    monto: Decimal = Field(gt=0)


class AbonoCrear(BaseModel):
    monto: Decimal = Field(gt=0)


class FiadoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_id: int
    venta_id: int | None
    monto: Decimal | None
    saldo: Decimal | None
    creado_en: datetime


class MovimientoFiadoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fiado_id: int
    tipo: str
    monto: Decimal
    creado_en: datetime


class DeudaLeer(BaseModel):
    cliente_id: int
    nombre: str
    saldo_fiado: Decimal
