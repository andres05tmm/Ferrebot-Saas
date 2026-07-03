"""Cuantización de dinero: una sola verdad de redondeo (NUMERIC(12,2), ROUND_HALF_UP).

FerreBot redondeaba a pesos enteros con `round()`; el SaaS conserva dos decimales
(migracion-puntorojo.md G2). Centralizado para que ventas e inventario no diverjan.
"""
from decimal import ROUND_HALF_UP, Decimal

CENTAVO = Decimal("0.01")


def cuantizar(valor: Decimal) -> Decimal:
    """Redondea a 2 decimales (centavos) con ROUND_HALF_UP."""
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def descomponer_iva(total_con_iva: Decimal, pct_iva: int | Decimal) -> tuple[Decimal, Decimal]:
    """(base, impuesto) de un total CON IVA incluido (estándar retail Colombia).

    UNA sola verdad de redondeo, base-primero (el orden de la factura electrónica: la UBL redondea
    la base y deriva el IVA por diferencia, pre-check FAU04). Ventas y facturación DEBEN usar esta
    función: redondear primero el impuesto puede diferir un centavo y descuadrar venta vs documento.
    """
    divisor = Decimal(1) + Decimal(pct_iva) / Decimal(100)
    base = cuantizar(total_con_iva / divisor)
    return base, cuantizar(total_con_iva - base)
