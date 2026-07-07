"""Máquinas — horas facturables de un alquiler (plan PIM §4).

Regla de negocio del cliente: cada servicio de máquina tiene un MÍNIMO de horas facturables.
Si la máquina trabaja menos que el mínimo, se le cobra el mínimo (costo fijo de movilización /
alistamiento); si trabaja más, se cobra lo trabajado. De ahí `max(horas, minimo)`.
"""
from __future__ import annotations

from decimal import Decimal


def horas_facturables(horas_trabajadas: Decimal, minimo_horas: Decimal) -> Decimal:
    """Horas a facturar = `max(horas_trabajadas, minimo_horas)`.

    [DEFINIR: si el mínimo aplica por servicio diario o por movilización] — el cliente aún no
    confirmó (plan §7). La firma no cambia según la respuesta: solo cambia con qué frecuencia
    el caller invoca esta función (una vez por día vs. una vez por movilización). No se redondea:
    las horas son la unidad de negocio, no un monto de dinero.
    """
    return max(horas_trabajadas, minimo_horas)
