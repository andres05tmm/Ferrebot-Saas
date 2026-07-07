"""Nómina — liquidación de trabajadores y prorrateo a obra (plan PIM §4).

STUBS TIPADOS (Fase 4). Las firmas y las dataclasses de retorno están esbozadas para que las
fases dependientes (obra, prorrateo, CUNE) tipen contra ellas; la implementación está
[DEFINIR contador]: los porcentajes y recargos legales (aportes empleador, fondo de solidaridad,
recargos de HE) los debe confirmar el contador de PIM antes de codificar el motor (plan §7).

Cuando lleguen los valores: se implementan estas funciones leyendo `ParametrosLegales` (vigencia
por fecha, nunca hardcodear SMMLV/aux/%s — skill money-safe), `Decimal` end-to-end y redondeo solo
al final. Invariantes con test-primero (plan §5): idempotencia de la liquidación y conciliación
exacta del prorrateo (Σ prorrateado ≡ costo total, sin pérdida ni duplicación).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class Liquidacion:
    """Resultado de liquidar a un trabajador en un periodo. Esbozo (Fase 4).

    Espeja `DetalleLiquidacion` (spec 01_MODELO_DATOS): devengados, deducciones, neto y aportes
    del empleador (estos no bajan al neto del trabajador; sirven al costeo real de la obra).
    """

    # Devengados
    salario_base: Decimal
    auxilio_transporte: Decimal
    valor_horas_extra: Decimal
    total_devengado: Decimal
    # Deducciones (empleado)
    salud_empleado: Decimal
    pension_empleado: Decimal
    total_deducciones: Decimal
    # Neto a pagar
    neto_pagar: Decimal
    # Aportes empleador + provisiones (costeo real, no van al neto)
    aportes_empleador: Decimal
    provisiones: Decimal


@dataclass(frozen=True, slots=True)
class ProrrateoObra:
    """Una porción del costo total de un trabajador imputada a una obra (o a admin). Esbozo.

    `obra_id = None` significa nómina administrativa (días no imputables a una obra concreta).
    Espeja `ProrrateoNominaObra` de la spec; DTO de la capa de cálculo, no el modelo ORM.
    """

    obra_id: str | None
    dias_imputados: Decimal
    costo_imputado: Decimal   # incluye prestaciones prorrateadas, no solo salario


def liquidar_directo(trabajador: Any, asistencia: Any, params: Any) -> Liquidacion:
    """Liquida a un trabajador DIRECTO en un periodo. [DEFINIR contador] (Fase 4).

    Regla (plan §4): salario proporcional (días/30); aux. transporte si salario ≤ 2 SMMLV;
    HE = (salario/240) × recargo; base de cotización SIN aux; salud/pensión 4%/4%; fondo de
    solidaridad por rangos; aportes empleador + provisiones (cesantías 8.33%, intereses 12% anual,
    prima 8.33%, vacaciones 4.17%). Recargos y %s son parámetros [DEFINIR contador].
    """
    raise NotImplementedError("Fase 4 — valores legales [DEFINIR contador] (plan §7)")


def liquidar_patacaliente(horas: Decimal, tarifa_hora: Decimal) -> Liquidacion:
    """Liquida a un trabajador PATACALIENTE (por hora). Fase 4.

    neto = horas × tarifa_hora; sin deducciones ni aportes ni CUNE (no es nómina electrónica).
    Ej.: 48 h × 12.000 → 576.000. Mecánica sin bloqueo legal, pero se implementa junto al resto
    del motor de nómina en Fase 4 para no partir la capa.
    """
    raise NotImplementedError("Fase 4 — motor de nómina")


def prorratear_nomina_obra(
    liquidacion: Liquidacion,
    dias_por_obra: dict[str | None, Decimal],
) -> list[ProrrateoObra]:
    """Reparte el costo total de una liquidación entre obras según los días trabajados. Fase 4.

    costo_dia = (devengado + aportes + provisiones) / días_totales; se agrupa por obra
    (clave `None` = administrativo). INVARIANTE (test-primero, plan §5): Σ de `costo_imputado`
    ≡ costo total de la liquidación, sin pérdida ni duplicación de centavos (el residuo de
    redondeo se ajusta en la última fila).
    """
    raise NotImplementedError("Fase 4 — conciliación de prorrateo (test-primero)")
