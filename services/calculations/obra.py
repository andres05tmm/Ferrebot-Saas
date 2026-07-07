"""Obra — gasto real y semáforo de rentabilidad (plan PIM §4, el diferenciador del producto).

STUB TIPADO (Fase 3). El corazón del sistema: sumar TODO lo que consume una obra (gastos, compras,
nómina prorrateada, horas de máquina, consumos de inventario) y compararlo con la utilidad
presupuestada para alertar ANTES de la pérdida (márgenes de 3–4%). La firma y `DesgloseGasto` se
esbozan aquí; la implementación va en Fase 3, cuando existan los modelos de obra/asignaciones/horas.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable


class Semaforo(str, Enum):
    """Estado de rentabilidad de una obra frente a la utilidad presupuestada (plan §4).

    VERDE: gasto real deja margen ≥ utilidad presupuestada. AMARILLO: margen entre 0 y la
    utilidad presupuestada (comiéndose la utilidad). ROJO: margen negativo (pérdida).
    """

    VERDE = "verde"
    AMARILLO = "amarillo"
    ROJO = "rojo"


@dataclass(frozen=True, slots=True)
class DesgloseGasto:
    """Gasto real de una obra desglosado por componente + total y semáforo. Esbozo (Fase 3)."""

    total_gastos: Decimal
    total_compras: Decimal
    total_prorrateo_nomina: Decimal
    total_horas_maquina: Decimal
    total_consumos_inventario: Decimal
    total: Decimal
    semaforo: Semaforo


def calcular_gasto_real_obra(
    gastos: Iterable[Any],
    compras: Iterable[Any],
    prorrateos: Iterable[Any],
    horas_maquina: Iterable[Any],
    costo_op_hora: Decimal,
    consumos: Iterable[Any],
) -> DesgloseGasto:
    """Gasto real de una obra en tiempo real + semáforo de rentabilidad. Fase 3.

    total = Σ gastos + Σ compras + Σ prorrateo_nómina + Σ(horas × costo_op_hora) + Σ(cant × costo_unit).
    Semáforo por umbral contra la utilidad presupuestada (verde ≥ U, amarillo 0–U, rojo < 0).
    `Decimal` end-to-end, redondeo solo al final. `costo_op_hora` sigue [DEFINIR] (costo interno
    por hora de máquina — el cliente aún no confirma si se rastrea rentabilidad neta, plan §7).
    """
    raise NotImplementedError("Fase 3 — requiere modelos de obra/asignaciones/horas")
