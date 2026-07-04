"""Motor PURO de retenciones/INC (ADR 0027): cálculo sin DB. Unidad testeable del dominio tributario.

Cubre: retefuente con umbral en UVT (aplica/no aplica), ICA por mil, reteIVA sobre el IVA, INC por %,
catálogo vacío → nada (opt-in), y los agregados (total_retenido excluye INC).
"""
from decimal import Decimal

from modules.retenciones.motor import (
    ReglaRetencion,
    calcular_retenciones,
    total_inc,
    total_retenido,
)


def _regla(tipo, concepto, tarifa, base_min="0"):
    return ReglaRetencion(
        tipo=tipo, concepto=concepto,
        base_minima_uvt=Decimal(base_min), tarifa=Decimal(str(tarifa)), activo=True,
    )


def test_catalogo_vacio_no_calcula_nada():
    # Opt-in: sin reglas, ningún renglón (y por tanto ningún total cambia).
    assert calcular_retenciones([], base_gravable=Decimal("1000000"), iva=Decimal("190000"), uvt_valor=Decimal("49799")) == []


def test_retefuente_porcentaje_sobre_base_gravable():
    reglas = [_regla("retefuente", "compras", "2.5")]
    r = calcular_retenciones(reglas, base_gravable=Decimal("1000000"), iva=Decimal("190000"), uvt_valor=Decimal("0"))
    assert len(r) == 1
    assert r[0].tipo == "retefuente"
    assert r[0].base == Decimal("1000000.00")
    assert r[0].valor == Decimal("25000.00")   # 1.000.000 × 2.5%


def test_retefuente_bajo_base_minima_no_retiene():
    # Base mínima 27 UVT × 49.799 = 1.344.573; base 1.000.000 < umbral → no retiene.
    reglas = [_regla("retefuente", "compras", "2.5", base_min="27")]
    r = calcular_retenciones(reglas, base_gravable=Decimal("1000000"), iva=Decimal("190000"), uvt_valor=Decimal("49799"))
    assert r == []


def test_retefuente_sobre_base_minima_si_retiene():
    reglas = [_regla("retefuente", "compras", "2.5", base_min="27")]
    r = calcular_retenciones(reglas, base_gravable=Decimal("2000000"), iva=Decimal("380000"), uvt_valor=Decimal("49799"))
    assert len(r) == 1 and r[0].valor == Decimal("50000.00")   # 2.000.000 × 2.5%


def test_ica_es_por_mil():
    reglas = [_regla("ica", "Cartagena", "7")]   # 7 por mil
    r = calcular_retenciones(reglas, base_gravable=Decimal("1000000"), iva=Decimal("0"), uvt_valor=Decimal("0"))
    assert r[0].valor == Decimal("7000.00")      # 1.000.000 × 7 / 1000


def test_reteiva_sobre_el_iva_no_sobre_la_base():
    reglas = [_regla("reteiva", "reteiva", "15")]
    r = calcular_retenciones(reglas, base_gravable=Decimal("1000000"), iva=Decimal("190000"), uvt_valor=Decimal("0"))
    assert r[0].base == Decimal("190000.00")     # la base del reteIVA es el IVA
    assert r[0].valor == Decimal("28500.00")     # 190.000 × 15%


def test_inc_porcentaje_pero_no_cuenta_como_retenido():
    reglas = [_regla("inc", "consumo", "8"), _regla("retefuente", "compras", "2.5")]
    r = calcular_retenciones(reglas, base_gravable=Decimal("1000000"), iva=Decimal("0"), uvt_valor=Decimal("0"))
    assert total_retenido(r) == Decimal("25000.00")   # solo la retefuente
    assert total_inc(r) == Decimal("80000.00")        # el INC va aparte


def test_reglas_inactivas_y_uvt_se_ignoran():
    reglas = [
        ReglaRetencion(tipo="retefuente", concepto="x", base_minima_uvt=Decimal("0"), tarifa=Decimal("2.5"), activo=False),
        ReglaRetencion(tipo="uvt", concepto="2026", base_minima_uvt=Decimal("0"), tarifa=Decimal("49799"), activo=True),
    ]
    assert calcular_retenciones(reglas, base_gravable=Decimal("1000000"), iva=Decimal("0"), uvt_valor=Decimal("49799")) == []
