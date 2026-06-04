"""Arqueo de caja — lógica pura del cierre (ferrebot-logica-portar.md §6).

FerreBot: `esperado = monto_apertura + ventas_efectivo − gastos` (caja_service.py:153).
El SaaS reconstruye con `caja_movimientos` explícitos: los gastos son egresos y puede haber
ingresos/egresos manuales; las ventas en efectivo se leen de la tabla `ventas` (decisión del
saldo_esperado híbrido, ver docs). Función pura: el repositorio le pasa los agregados ya sumados.

GREEN pendiente: este módulo es un stub para la fase RED (tests de paridad primero).
"""
from dataclasses import dataclass
from decimal import Decimal

from core.money import cuantizar


@dataclass(frozen=True, slots=True)
class Arqueo:
    saldo_esperado: Decimal
    diferencia: Decimal   # saldo_contado − saldo_esperado (negativo = faltante, positivo = sobrante)


def calcular_arqueo(
    *,
    saldo_inicial: Decimal,
    ventas_efectivo: Decimal,
    ingresos: Decimal,
    egresos: Decimal,
    saldo_contado: Decimal,
) -> Arqueo:
    """(saldo_esperado, diferencia) del cierre.

    GUARDRAIL anti-doble-conteo: `egresos` es Σ de `caja_movimientos` egreso, que YA incluye los
    gastos (cada gasto postea su egreso). No existe parámetro `gastos`: la tabla `gastos` nunca se
    resta aparte. Fuente única = `caja_movimientos`.
    """
    esperado = cuantizar(saldo_inicial + ventas_efectivo + ingresos - egresos)
    diferencia = cuantizar(saldo_contado - esperado)
    return Arqueo(saldo_esperado=esperado, diferencia=diferencia)
