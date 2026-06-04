"""Cuantización de dinero: una sola verdad de redondeo (NUMERIC(12,2), ROUND_HALF_UP).

FerreBot redondeaba a pesos enteros con `round()`; el SaaS conserva dos decimales
(migracion-puntorojo.md G2). Centralizado para que ventas e inventario no diverjan.
"""
from decimal import ROUND_HALF_UP, Decimal

CENTAVO = Decimal("0.01")


def cuantizar(valor: Decimal) -> Decimal:
    """Redondea a 2 decimales (centavos) con ROUND_HALF_UP."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)
