"""Errores del dominio de nómina (Fase 4 PIM). Los mapea a HTTP el router."""
from __future__ import annotations


class NominaError(Exception):
    """Base del dominio de nómina."""


class PeriodoNominaInexistente(NominaError):
    """No existe un `periodos_nomina` con ese id (404)."""

    def __init__(self, periodo_id: int) -> None:
        super().__init__(f"periodo de nómina {periodo_id} inexistente")
        self.periodo_id = periodo_id


class ParametrosLegalesInexistentes(NominaError):
    """No hay una fila de `parametros_legales` vigente que congelar al crear el periodo (409).

    Es un prerrequisito de provisioning: el pack construcción siembra los parámetros 2026. Sin ellos el
    motor no puede liquidar (jamás inventa valores legales).
    """


class PeriodoBloqueado(NominaError):
    """La acción no aplica al estado del periodo (409).

    Re-liquidar un periodo ya cerrado (LIQUIDADO/PAGADO), o pagar uno que no está LIQUIDADO. El cierre y
    el pago SÍ son idempotentes sobre su propio estado (reintentar = replay, sin error): este error es
    para transiciones realmente inválidas, no para reintentos.
    """


class TrabajadorNoLiquidable(NominaError):
    """Un trabajador sin los datos mínimos para liquidar su vínculo (DIRECTO sin salario base o
    PATACALIENTE sin tarifa/hora). 422 en el router."""

    def __init__(self, trabajador_id: int, motivo: str) -> None:
        super().__init__(f"trabajador {trabajador_id} no liquidable: {motivo}")
        self.trabajador_id = trabajador_id
        self.motivo = motivo
