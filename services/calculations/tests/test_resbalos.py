"""Aceptación resbalo (plan PIM §4): margen de viaje de material y alerta de baja rentabilidad."""
from decimal import Decimal

from services.calculations.resbalos import calcular_resbalo


def test_caso_brief_150000_y_13_04_pct():
    # costo 1.000.000, venta 1.150.000 → 150.000 y 13,04% (150.000 / 1.150.000, sobre la venta).
    resbalo = calcular_resbalo(
        precio_venta_cliente=Decimal("1150000"),
        costo_total_compra=Decimal("1000000"),
    )
    assert resbalo.monto == Decimal("150000.00")
    assert resbalo.porcentaje == Decimal("13.04")
    assert resbalo.alerta is False   # 13,04% > 5%


def test_resbalo_negativo_dispara_alerta():
    # Venta por debajo del costo: pérdida → monto negativo, alerta True.
    resbalo = calcular_resbalo(
        precio_venta_cliente=Decimal("900000"),
        costo_total_compra=Decimal("1000000"),
    )
    assert resbalo.monto == Decimal("-100000.00")
    assert resbalo.porcentaje < Decimal("0")
    assert resbalo.alerta is True


def test_margen_bajo_umbral_dispara_alerta():
    # Margen positivo pero por debajo del 5%: 3% → alerta.
    resbalo = calcular_resbalo(
        precio_venta_cliente=Decimal("1000000"),
        costo_total_compra=Decimal("970000"),
    )
    assert resbalo.monto == Decimal("30000.00")
    assert resbalo.porcentaje == Decimal("3.00")
    assert resbalo.alerta is True


def test_margen_justo_en_el_umbral_no_alerta():
    # Borde: exactamente 5% NO alerta (alerta es estrictamente < 5%).
    resbalo = calcular_resbalo(
        precio_venta_cliente=Decimal("1000000"),
        costo_total_compra=Decimal("950000"),
    )
    assert resbalo.porcentaje == Decimal("5.00")
    assert resbalo.alerta is False


def test_venta_cero_alerta_forzada_sin_dividir_por_cero():
    resbalo = calcular_resbalo(
        precio_venta_cliente=Decimal("0"),
        costo_total_compra=Decimal("500000"),
    )
    assert resbalo.monto == Decimal("-500000.00")
    assert resbalo.porcentaje == Decimal("0.00")
    assert resbalo.alerta is True
