"""Schemas del motor contable (ADR 0030): entrada de asientos y salida de estados financieros.

Los montos viajan como `Decimal`; el redondeo único es `core.money.cuantizar`. La validación
débitos=créditos NO vive aquí (es app-layer en el servicio, con la naturaleza de las cuentas a la
vista): el schema solo garantiza `amount > 0` y `direction` válida.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LineaAsiento(BaseModel):
    """Una línea de asiento: cuenta imputable + dirección + monto sin signo."""

    cuenta_codigo: str
    direction: str
    amount: Decimal = Field(gt=0)
    descripcion: str | None = None

    @field_validator("direction")
    @classmethod
    def _dir_valida(cls, v: str) -> str:
        if v not in ("debit", "credit"):
            raise ValueError("direction debe ser 'debit' o 'credit'")
        return v


class AsientoCrear(BaseModel):
    """Cabecera + líneas de un asiento a registrar. `idempotency_key` ancla la idempotencia."""

    fecha: datetime
    origen_tipo: str
    origen_id: int | None = None
    descripcion: str | None = None
    idempotency_key: str | None = None
    lineas: list[LineaAsiento]


class LineaAsientoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    cuenta_codigo: str
    cuenta_nombre: str
    direction: str
    amount: Decimal
    descripcion: str | None = None


class AsientoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    fecha: datetime
    estado: str
    origen_tipo: str
    origen_id: int | None
    descripcion: str | None
    reverso_de: int | None
    lineas: list[LineaAsientoLeer]


class FilaBalanceComprobacion(BaseModel):
    codigo: str
    nombre: str
    naturaleza: str
    debitos: Decimal
    creditos: Decimal
    saldo: Decimal


class BalanceComprobacion(BaseModel):
    """Balance de comprobación: filas por cuenta imputable + totales de cuadre."""

    filas: list[FilaBalanceComprobacion]
    total_debitos: Decimal
    total_creditos: Decimal
    cuadra: bool


class FilaEstado(BaseModel):
    codigo: str
    nombre: str
    valor: Decimal


class EstadoResultados(BaseModel):
    ingresos: list[FilaEstado]
    costos: list[FilaEstado]
    gastos: list[FilaEstado]
    total_ingresos: Decimal
    total_costos: Decimal
    total_gastos: Decimal
    utilidad: Decimal


class BalanceGeneral(BaseModel):
    activos: list[FilaEstado]
    pasivos: list[FilaEstado]
    patrimonio: list[FilaEstado]
    total_activos: Decimal
    total_pasivos: Decimal
    total_patrimonio: Decimal
    utilidad_ejercicio: Decimal
    cuadra: bool


class FilaFlujo(BaseModel):
    concepto: str
    valor: Decimal


class FlujoEfectivo(BaseModel):
    """Flujo de efectivo (método directo, simplificado): movimientos de Caja+Bancos por origen."""

    entradas: list[FilaFlujo]
    salidas: list[FilaFlujo]
    total_entradas: Decimal
    total_salidas: Decimal
    flujo_neto: Decimal
