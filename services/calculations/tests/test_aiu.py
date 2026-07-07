"""Aceptación AIU (plan PIM §4): IVA solo sobre la utilidad. Caso manual del brief."""
from dataclasses import dataclass
from decimal import Decimal

from services.calculations.aiu import calcular_totales_cotizacion


@dataclass(frozen=True)
class _Linea:
    """Ítem de prueba con el contrato mínimo (cantidad × valor_unitario)."""

    cantidad: Decimal
    valor_unitario: Decimal


def test_caso_brief_iva_solo_sobre_utilidad():
    # subtotal 10.000.000, A 5%, I 3%, U 4%, IVA 19% sobre la utilidad.
    items = [_Linea(Decimal("1"), Decimal("10000000"))]
    totales = calcular_totales_cotizacion(
        items,
        administracion_pct=Decimal("0.05"),
        imprevistos_pct=Decimal("0.03"),
        utilidad_pct=Decimal("0.04"),
        iva_sobre_utilidad_pct=Decimal("0.19"),
    )
    assert totales.subtotal == Decimal("10000000.00")
    assert totales.administracion == Decimal("500000.00")
    assert totales.imprevistos == Decimal("300000.00")
    assert totales.utilidad == Decimal("400000.00")
    assert totales.iva_utilidad == Decimal("76000.00")   # 400.000 × 0,19 (NO sobre el subtotal)
    assert totales.total == Decimal("11276000.00")


def test_subtotal_suma_varias_lineas():
    items = [
        _Linea(Decimal("2"), Decimal("2500000")),   # 5.000.000
        _Linea(Decimal("5"), Decimal("1000000")),   # 5.000.000
    ]
    totales = calcular_totales_cotizacion(
        items,
        administracion_pct=Decimal("0.05"),
        imprevistos_pct=Decimal("0.03"),
        utilidad_pct=Decimal("0.04"),
        iva_sobre_utilidad_pct=Decimal("0.19"),
    )
    assert totales.subtotal == Decimal("10000000.00")
    assert totales.total == Decimal("11276000.00")


def test_items_vacio_todo_en_cero():
    totales = calcular_totales_cotizacion(
        [],
        administracion_pct=Decimal("0.05"),
        imprevistos_pct=Decimal("0.03"),
        utilidad_pct=Decimal("0.04"),
        iva_sobre_utilidad_pct=Decimal("0.19"),
    )
    assert totales.subtotal == Decimal("0.00")
    assert totales.administracion == Decimal("0.00")
    assert totales.imprevistos == Decimal("0.00")
    assert totales.utilidad == Decimal("0.00")
    assert totales.iva_utilidad == Decimal("0.00")
    assert totales.total == Decimal("0.00")


def test_pcts_cero_total_igual_subtotal():
    items = [_Linea(Decimal("1"), Decimal("10000000"))]
    totales = calcular_totales_cotizacion(
        items,
        administracion_pct=Decimal("0"),
        imprevistos_pct=Decimal("0"),
        utilidad_pct=Decimal("0"),
        iva_sobre_utilidad_pct=Decimal("0.19"),
    )
    # Sin utilidad no hay IVA (grava solo la utilidad): total == subtotal.
    assert totales.utilidad == Decimal("0.00")
    assert totales.iva_utilidad == Decimal("0.00")
    assert totales.total == Decimal("10000000.00")
