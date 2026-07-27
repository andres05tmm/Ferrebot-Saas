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
# 'abono' es el pago a un PROVEEDOR (`facturas_abonos`); 'abono_fiado', el pago de un CLIENTE a su
# fiado (`fiados_movimientos`). Tablas distintas: compartir el nombre cruzaría sus ids en el enlace.
TipoInterno = Literal["venta", "gasto", "abono", "abono_fiado"]
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
    cliente: str | None = None      # para reconocer de quién es el pago al resolver un ambiguo


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
    # Lo que el dueño necesita para reconocer el pago de un vistazo: quién mandó, a qué cuenta y a
    # qué hora. Estaba en la tabla desde 0001 y nunca salía por el API.
    remitente: str | None = None
    hora: str | None = None
    tipo_transaccion: str | None = None
    cuenta_destino: str | None = None
    descartado_en: datetime | None = None
    origen: str = "extracto"      # property del modelo: 'gmail' | 'extracto'


class MovimientoConCandidatos(BaseModel):
    """Movimiento bancario + sus candidatos internos (para resolver los ambiguos a mano)."""

    movimiento: MovimientoBancarioLeer
    candidatos: list[CandidatoInterno]


class TotalPorCuenta(BaseModel):
    """Cuánto entró a UNA cuenta en el período."""

    cuenta: str | None            # None = el parser no la pudo leer ("sin identificar")
    alias: str | None = None      # nombre del titular, si la empresa lo configuró
    movimientos: int
    total: Decimal
    total_negocio: Decimal


class TotalesBancarios(BaseModel):
    """Cuánta plata entró a las cuentas del negocio en un período (solo créditos).

    `total` = todo lo que entró. `total_negocio` descuenta lo marcado "no es venta" y
    `total_personal` es justo esa diferencia, así que `total == total_negocio + total_personal`
    siempre. `sin_clasificar` cuenta los movimientos que nadie resolvió todavía.
    """

    desde: date
    hasta: date
    total: Decimal
    total_negocio: Decimal
    total_personal: Decimal
    sin_clasificar: int
    por_cuenta: list[TotalPorCuenta]


class ConciliarConfirmar(BaseModel):
    """Confirmación EXPLÍCITA del enlace elegido (sugerido/ambiguo → conciliado)."""

    tipo: TipoInterno
    id_interno: int
