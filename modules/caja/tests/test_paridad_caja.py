"""Paridad de caja: el arqueo del SaaS reproduce la fórmula de FerreBot (§6, caja_service.py:153)."""
from decimal import Decimal

from modules.caja.arqueo import calcular_arqueo


def test_esperado_reproduce_formula_ferrebot():
    # FerreBot: esperado = apertura + ventas_efectivo − gastos.
    # En el SaaS los gastos son egresos y aquí no hay ingresos manuales (egresos == gastos).
    arqueo = calcular_arqueo(
        saldo_inicial=Decimal("100000"),
        ventas_efectivo=Decimal("250000"),
        ingresos=Decimal("0"),
        egresos=Decimal("40000"),          # = gastos de caja
        saldo_contado=Decimal("310000"),
    )
    assert arqueo.saldo_esperado == Decimal("310000.00")   # 100000 + 250000 − 40000
    assert arqueo.diferencia == Decimal("0.00")


def test_diferencia_faltante_y_sobrante():
    faltante = calcular_arqueo(
        saldo_inicial=Decimal("0"), ventas_efectivo=Decimal("100000"),
        ingresos=Decimal("0"), egresos=Decimal("0"), saldo_contado=Decimal("95000"),
    )
    assert faltante.diferencia == Decimal("-5000.00")      # contado < esperado → faltante

    sobrante = calcular_arqueo(
        saldo_inicial=Decimal("0"), ventas_efectivo=Decimal("100000"),
        ingresos=Decimal("0"), egresos=Decimal("0"), saldo_contado=Decimal("103000"),
    )
    assert sobrante.diferencia == Decimal("3000.00")       # contado > esperado → sobrante


def test_ingresos_y_egresos_manuales_entran_al_esperado():
    # caja_movimientos manuales (ingreso/egreso) también cuentan en el esperado del SaaS.
    arqueo = calcular_arqueo(
        saldo_inicial=Decimal("50000"), ventas_efectivo=Decimal("0"),
        ingresos=Decimal("20000"), egresos=Decimal("15000"), saldo_contado=Decimal("55000"),
    )
    assert arqueo.saldo_esperado == Decimal("55000.00")    # 50000 + 20000 − 15000
    assert arqueo.diferencia == Decimal("0.00")
