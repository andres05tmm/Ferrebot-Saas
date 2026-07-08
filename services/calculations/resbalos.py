"""Resbalos — margen de un viaje de material (plan PIM §4, spec módulo 11).

"Resbalo" es la jerga del cliente para el margen de un viaje de material (asfalto/arena):
lo que le cobra al cliente por el viaje menos lo que le paga al proveedor. Solo aplica a
compras con `es_viaje_material = true`. Con márgenes de 3–4%, un resbalo bajo o negativo es una
pérdida silenciosa, por eso se marca una alerta cuando el porcentaje cae por debajo del umbral.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from core.money import cuantizar

# Umbral de rentabilidad mínima de un viaje (%). Por debajo (o negativo) se alerta al dueño.
UMBRAL_ALERTA_PCT: Decimal = Decimal("5")


@dataclass(frozen=True, slots=True)
class Resbalo:
    """Margen de un viaje de material. `porcentaje` es sobre el precio de venta al cliente."""

    monto: Decimal        # precio_venta_cliente − costo_total_compra
    porcentaje: Decimal   # monto / precio_venta_cliente × 100, cuantizado a 2 decimales
    alerta: bool          # True si porcentaje < UMBRAL_ALERTA_PCT (incluye margen negativo)


def calcular_resbalo(
    precio_venta_cliente: Decimal,
    costo_total_compra: Decimal,
) -> Resbalo:
    """Margen (monto y %) de un viaje de material y si dispara alerta de baja rentabilidad.

    `porcentaje = monto / precio_venta_cliente × 100` (margen sobre la VENTA, no sobre el costo):
    costo 1.000.000 y venta 1.150.000 → monto 150.000 y 13,04% (150.000 / 1.150.000). Se cuantiza
    a 2 decimales, la misma precisión que un porcentaje de factura.

    Guardas: si `precio_venta_cliente <= 0` no hay venta válida → porcentaje 0 y alerta forzada
    (no se puede dividir por cero y un viaje sin precio de venta es, por definición, sospechoso).
    """
    monto = precio_venta_cliente - costo_total_compra

    if precio_venta_cliente <= 0:
        return Resbalo(monto=cuantizar(monto), porcentaje=Decimal("0.00"), alerta=True)

    porcentaje = cuantizar(monto / precio_venta_cliente * Decimal("100"))
    return Resbalo(
        monto=cuantizar(monto),
        porcentaje=porcentaje,
        alerta=porcentaje < UMBRAL_ALERTA_PCT,
    )
