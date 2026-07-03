"""Contratos Pydantic de caja y gastos (api-contract.md §caja/gastos)."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CajaMovTipo = Literal["ingreso", "egreso"]
GastoCategoria = Literal["transporte", "papeleria", "servicios", "nomina", "mantenimiento", "otros"]


class AperturaCrear(BaseModel):
    saldo_inicial: Decimal = Field(ge=0)


class CierreCrear(BaseModel):
    saldo_contado: Decimal = Field(ge=0)


class MovimientoCrear(BaseModel):
    tipo: CajaMovTipo
    monto: Decimal = Field(gt=0)
    concepto: str | None = None


class GastoCrear(BaseModel):
    categoria: GastoCategoria
    monto: Decimal = Field(gt=0)
    concepto: str | None = None
    # Vínculo opcional a cuentas por pagar (ADR 0028): a quién se le pagó y qué factura salda este
    # gasto. Con `factura_proveedor_id`, el gasto genera SU único abono (no se registra otro aparte).
    proveedor_id: int | None = None
    factura_proveedor_id: str | None = None


class CajaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    usuario_id: int | None
    fecha_apertura: datetime
    saldo_inicial: Decimal
    fecha_cierre: datetime | None
    saldo_esperado: Decimal | None
    saldo_contado: Decimal | None
    diferencia: Decimal | None
    estado: str


class MovimientoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    caja_id: int
    tipo: str
    monto: Decimal
    concepto: str | None
    referencia: str | None
    creado_en: datetime


class GastoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    categoria: str
    monto: Decimal
    concepto: str | None
    caja_id: int | None
    usuario_id: int | None
    proveedor_id: int | None = None
    factura_proveedor_id: str | None = None
    abono_proveedor_id: int | None = None
    creado_en: datetime
