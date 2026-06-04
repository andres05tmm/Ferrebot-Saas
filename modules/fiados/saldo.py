"""Saldo de fiado — lógica pura del ledger (ferrebot-logica-portar.md §6, schema.md).

`fiados_movimientos` es la FUENTE DE VERDAD: `saldo = Σ(cargos) − Σ(abonos)`. `fiados.saldo` y
`clientes.saldo_fiado` son contadores denormalizados que se actualizan en la MISMA transacción
(dual-write atómico); deben coincidir siempre con el ledger.

FerreBot (fiados_service.py:70): `saldo_nuevo = saldo_anterior + cargo − abono`.

GREEN pendiente: stub para la fase RED.
"""
from collections.abc import Iterable
from decimal import Decimal

from core.money import cuantizar

CARGO = "cargo"
ABONO = "abono"


def nuevo_saldo(saldo_anterior: Decimal, tipo: str, monto: Decimal) -> Decimal:
    """Aplica un movimiento al contador: cargo suma, abono resta."""
    if tipo == CARGO:
        return cuantizar(saldo_anterior + monto)
    if tipo == ABONO:
        return cuantizar(saldo_anterior - monto)
    raise ValueError(f"tipo de movimiento de fiado inválido: {tipo!r}")


def saldo_desde_movimientos(movimientos: Iterable[tuple[str, Decimal]]) -> Decimal:
    """Recalcula el saldo desde el ledger: Σ(cargos) − Σ(abonos). Fuente de verdad."""
    total = Decimal("0")
    for tipo, monto in movimientos:
        total = nuevo_saldo(total, tipo, monto)
    return cuantizar(total)


def excede_saldo(saldo_anterior: Decimal, monto_abono: Decimal) -> bool:
    """True si el abono supera el saldo (sobre-abono → el servicio responde 422)."""
    return monto_abono > saldo_anterior
