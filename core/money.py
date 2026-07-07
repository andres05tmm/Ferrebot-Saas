"""Cuantización de dinero: una sola verdad de redondeo (NUMERIC(12,2), ROUND_HALF_UP).

FerreBot redondeaba a pesos enteros con `round()`; el SaaS conserva dos decimales
(migracion-puntorojo.md G2). Centralizado para que ventas e inventario no diverjan.
"""
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Numeric

CENTAVO = Decimal("0.01")

# Dos precisiones de dinero CONVIVEN en la plataforma y NO se deben mezclar (riesgo técnico §7 del
# plan PIM). Se declaran juntas para que la divergencia quede a la vista:
#   MONEY  = NUMERIC(12,2) — POS retail (pesos con centavos). Precisión histórica de FerreBot; los
#            modelos del POS la declaran inline (`MONEY = Numeric(12, 2)`) y `cuantizar` redondea ahí.
#   MONEY4 = NUMERIC(18,4) — vertical CONSTRUCCIÓN (spec cliente 01_MODELO_DATOS: `@db.Decimal(18,4)`).
#            AIU con márgenes de 3–4% y prorrateo de nómina necesitan 4 decimales para conciliar sin
#            perder centavos. Se estrena en la migración tenant 0043 (Construcciones PIM).
MONEY = Numeric(12, 2)
MONEY4 = Numeric(18, 4)


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
