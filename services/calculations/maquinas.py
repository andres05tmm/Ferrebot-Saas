"""Máquinas — horas facturables de un alquiler (plan PIM §4).

Regla de negocio del cliente: cada servicio de máquina tiene un MÍNIMO de horas facturables.
Si la máquina trabaja menos que el mínimo, se le cobra el mínimo (costo fijo de movilización /
alistamiento); si trabaja más, se cobra lo trabajado. De ahí `max(horas, minimo)`.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal


def horas_transcurridas(inicio: datetime, fin: datetime) -> Decimal:
    """Horas entre dos instantes, para el cronómetro de operación en vivo (feature PIM).

    `(fin − inicio)` en horas como Decimal, cuantizado a 4 decimales (la precisión de horas del vertical).
    Nunca negativo: si `fin <= inicio` (reloj hacia atrás o instante idéntico) devuelve 0. El resultado es
    solo la PROPUESTA del reloj; el supervisor la confirma/ajusta antes de facturar.
    """
    segundos = Decimal((fin - inicio).total_seconds())
    if segundos <= 0:
        return Decimal("0")
    return (segundos / Decimal(3600)).quantize(Decimal("0.0001"))


def horas_facturables(horas_trabajadas: Decimal, minimo_horas: Decimal) -> Decimal:
    """Horas a facturar = `max(horas_trabajadas, minimo_horas)`.

    [DEFINIR: si el mínimo aplica por servicio diario o por movilización] — el cliente aún no
    confirmó (plan §7). La firma no cambia según la respuesta: solo cambia con qué frecuencia
    el caller invoca esta función (una vez por día vs. una vez por movilización). No se redondea:
    las horas son la unidad de negocio, no un monto de dinero.
    """
    return max(horas_trabajadas, minimo_horas)
