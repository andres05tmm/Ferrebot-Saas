"""Schemas Pydantic de conciliación bancaria (ADR 0028). Validación de toda entrada.

La ingesta exige `referencia_bancaria` (el ancla de idempotencia) y una `naturaleza` explícita; sin
ellas no hay ni dedup ni dirección de match. Los montos son Decimal (centavos), fechas en día
calendario (hora Colombia la resuelve el llamador).
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Naturaleza = Literal["credito", "debito"]
TipoInterno = Literal["venta", "gasto", "abono"]
EstadoConciliacion = Literal["no_conciliado", "sugerido", "conciliado"]


class MovimientoBancarioIngesta(BaseModel):
    """Una línea del extracto bancario a ingerir (idempotente por `referencia_bancaria`)."""

    referencia_bancaria: str = Field(min_length=1)
    fecha: date
    monto: Decimal = Field(gt=0)
    naturaleza: Naturaleza
    descripcion: str | None = None
    remitente: str | None = None


class IngestaResultado(BaseModel):
    """Cuántas líneas se insertaron vs. se saltaron por ya existir (idempotencia)."""

    insertados: int
    duplicados: int


class CandidatoInterno(BaseModel):
    """Un movimiento interno que calza por monto+fecha (posible contraparte de la conciliación)."""

    tipo: TipoInterno
    id: int
    monto: Decimal
    fecha: date
    descripcion: str | None = None


class MovimientoBancarioLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    referencia_bancaria: str | None
    fecha: date
    monto: Decimal
    naturaleza: str
    estado_conciliacion: EstadoConciliacion
    conciliado_con_tipo: str | None
    conciliado_con_id: int | None
    conciliado_en: datetime | None


class MovimientoConCandidatos(BaseModel):
    """Movimiento bancario + sus candidatos internos (para resolver los ambiguos a mano)."""

    movimiento: MovimientoBancarioLeer
    candidatos: list[CandidatoInterno]


class ConciliarConfirmar(BaseModel):
    """Confirmación EXPLÍCITA del enlace elegido (sugerido/ambiguo → conciliado)."""

    tipo: TipoInterno
    id_interno: int
