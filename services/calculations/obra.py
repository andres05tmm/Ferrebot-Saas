"""Obra — gasto real y semáforo de rentabilidad (plan PIM §4, el diferenciador del producto).

El corazón del sistema: sumar TODO lo que consume una obra (gastos, compras, nómina prorrateada,
horas de máquina, consumos de inventario) y compararlo con lo presupuestado para alertar ANTES de
la pérdida (márgenes de 3–4%). Función pura money-safe: `Decimal` end-to-end, redondeo SOLO al final
con `core.money.cuantizar`; UI, dashboard y bot llaman aquí.

Inputs por Protocol/duck typing, NO por el ORM: cada iterable es de objetos con los atributos del
contrato correspondiente (`GastoObra.monto`, `CompraObra.costo_total`, `ProrrateoImputado.costo_imputado`
—lo produce `nomina.prorratear_nomina_obra`—, `HorasRegistradas.horas`, `ConsumoObra.cantidad/costo_unitario`).
En Fase 3 el caller pasa los modelos ORM de obra; la función no depende de ellos.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Iterable, Protocol

from core.money import cuantizar


class Semaforo(str, Enum):
    """Estado de rentabilidad de una obra frente a la utilidad presupuestada (plan §4).

    VERDE: el margen restante (ingreso presupuestado − gasto real) aún cubre la utilidad
    presupuestada. AMARILLO: el margen es positivo pero por debajo de la utilidad (se la está
    comiendo). ROJO: el margen es negativo (pérdida).
    """

    VERDE = "verde"
    AMARILLO = "amarillo"
    ROJO = "rojo"


class GastoObra(Protocol):
    """Gasto imputado a la obra (duck typing): monto del gasto de caja."""

    monto: Decimal


class CompraObra(Protocol):
    """Compra imputada a la obra (duck typing): costo total de la compra (no mueve stock)."""

    costo_total: Decimal


class ProrrateoImputado(Protocol):
    """Fila de nómina prorrateada a la obra (duck typing). La produce `prorratear_nomina_obra`."""

    costo_imputado: Decimal


class HorasRegistradas(Protocol):
    """Registro de horas de máquina en la obra (duck typing): horas a costear (ya facturables)."""

    horas: Decimal


class ConsumoObra(Protocol):
    """Consumo de inventario en la obra (duck typing): cantidad × costo unitario."""

    cantidad: Decimal
    costo_unitario: Decimal


@dataclass(frozen=True, slots=True)
class DesgloseGasto:
    """Gasto real de una obra desglosado por componente + total y semáforo. Salida cuantizada."""

    total_gastos: Decimal
    total_compras: Decimal
    total_prorrateo_nomina: Decimal
    total_horas_maquina: Decimal
    total_consumos_inventario: Decimal
    total: Decimal
    semaforo: Semaforo


def calcular_gasto_real_obra(
    gastos: Iterable[GastoObra],
    compras: Iterable[CompraObra],
    prorrateos: Iterable[ProrrateoImputado],
    horas_maquina: Iterable[HorasRegistradas],
    costo_op_hora: Decimal,
    consumos: Iterable[ConsumoObra],
    ingreso_presupuestado: Decimal,
    utilidad_presupuestada: Decimal,
) -> DesgloseGasto:
    """Gasto real de una obra en tiempo real + semáforo de rentabilidad (plan §4).

    total = Σ gastos + Σ compras + Σ prorrateo_nómina + (Σ horas × costo_op_hora) + Σ(cantidad × costo_unit).

    Semáforo por margen restante `= ingreso_presupuestado − total` contra la utilidad presupuestada
    (plan §4: verde ≥ U, amarillo 0–U, rojo < 0):
      - ROJO    si margen < 0 (pérdida);
      - AMARILLO si 0 ≤ margen < utilidad_presupuestada (comiéndose la utilidad);
      - VERDE    si margen ≥ utilidad_presupuestada.

    Nota de firma (deliberada): el plan §4 lista la firma sin el presupuesto, pero el semáforo es
    imposible sin un umbral. Se agregan `ingreso_presupuestado` (valor del contrato/cotización GANADA)
    y `utilidad_presupuestada` (la U de la cotización) como entradas; el caller de Fase 3 los tiene a
    la mano desde la obra. `costo_op_hora` (costo interno por hora de máquina) sigue [DEFINIR] si el
    cliente rastrea rentabilidad neta (plan §7); es 0 si no se costea.

    Redondeo SOLO al final: los componentes se suman con precisión plena y se cuantizan al construir
    el resultado. Iterables vacíos → componente 0.
    """
    total_gastos = sum((g.monto for g in gastos), start=Decimal("0"))
    total_compras = sum((c.costo_total for c in compras), start=Decimal("0"))
    total_prorrateo = sum((p.costo_imputado for p in prorrateos), start=Decimal("0"))
    total_horas = sum((h.horas for h in horas_maquina), start=Decimal("0"))
    total_horas_maquina = total_horas * costo_op_hora
    total_consumos = sum(
        (c.cantidad * c.costo_unitario for c in consumos), start=Decimal("0")
    )

    total = (
        total_gastos
        + total_compras
        + total_prorrateo
        + total_horas_maquina
        + total_consumos
    )

    margen = ingreso_presupuestado - total
    if margen < 0:
        semaforo = Semaforo.ROJO
    elif margen < utilidad_presupuestada:
        semaforo = Semaforo.AMARILLO
    else:
        semaforo = Semaforo.VERDE

    return DesgloseGasto(
        total_gastos=cuantizar(total_gastos),
        total_compras=cuantizar(total_compras),
        total_prorrateo_nomina=cuantizar(total_prorrateo),
        total_horas_maquina=cuantizar(total_horas_maquina),
        total_consumos_inventario=cuantizar(total_consumos),
        total=cuantizar(total),
        semaforo=semaforo,
    )
