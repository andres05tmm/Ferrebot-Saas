"""Paridad de fiados: el saldo reproduce FerreBot y el ledger es la fuente de verdad (§6, schema.md)."""
from decimal import Decimal

from modules.fiados.saldo import (
    ABONO,
    CARGO,
    excede_saldo,
    nuevo_saldo,
    saldo_desde_movimientos,
)


def test_cargo_suma_al_saldo():
    # FerreBot: saldo_nuevo = saldo_anterior + cargo.
    assert nuevo_saldo(Decimal("10000"), CARGO, Decimal("5000")) == Decimal("15000.00")


def test_abono_resta_del_saldo():
    # FerreBot: saldo_nuevo = saldo_anterior − abono.
    assert nuevo_saldo(Decimal("15000"), ABONO, Decimal("6000")) == Decimal("9000.00")


def test_ledger_es_la_fuente_de_verdad():
    # saldo = Σ(cargos) − Σ(abonos): así se recalcula clientes.saldo_fiado / fiados.saldo.
    movimientos = [
        (CARGO, Decimal("20000")),
        (ABONO, Decimal("5000")),
        (CARGO, Decimal("10000")),
        (ABONO, Decimal("8000")),
    ]
    assert saldo_desde_movimientos(movimientos) == Decimal("17000.00")   # 20000−5000+10000−8000


def test_contador_secuencial_coincide_con_el_ledger():
    # Aplicar movimientos uno a uno (el contador denormalizado) == recomputar desde el ledger.
    movimientos = [(CARGO, Decimal("20000")), (ABONO, Decimal("5000")), (CARGO, Decimal("10000"))]
    contador = Decimal("0")
    for tipo, monto in movimientos:
        contador = nuevo_saldo(contador, tipo, monto)
    assert contador == saldo_desde_movimientos(movimientos)


def test_sobre_abono_detectado():
    assert excede_saldo(Decimal("5000"), Decimal("6000")) is True    # abono > saldo → 422
    assert excede_saldo(Decimal("5000"), Decimal("5000")) is False   # abono == saldo → permitido
