"""Aceptación horas facturables (plan PIM §4): max(trabajadas, mínimo)."""
from decimal import Decimal

from services.calculations.maquinas import horas_facturables


def test_bajo_el_minimo_cobra_el_minimo():
    assert horas_facturables(Decimal("3"), Decimal("5")) == Decimal("5")


def test_sobre_el_minimo_cobra_lo_trabajado():
    assert horas_facturables(Decimal("6"), Decimal("5")) == Decimal("6")


def test_exacto_en_el_minimo():
    # Borde: trabajar justo el mínimo factura el mínimo (max coincide).
    assert horas_facturables(Decimal("5"), Decimal("5")) == Decimal("5")
