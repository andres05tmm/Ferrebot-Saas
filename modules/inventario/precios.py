"""Motor de precios — port de `catalogo_service.py:315` (ferrebot-logica-portar.md §3).

Función pura `obtener_precio_para_cantidad`: dado el esquema de precio de un producto y una
cantidad, devuelve (total_de_la_linea, precio_unitario). Tres esquemas que conviven, evaluados
SIEMPRE en este orden:

    1. Escalonado por umbral  → precio_sobre_umbral si cantidad >= umbral, si no precio_bajo_umbral.
    2. Por fracción           → si alguna fracción coincide (|decimal - cantidad| < 0.01).
    3. Simple                 → precio_venta * cantidad.

Sin SQL: el repositorio arma el `EsquemaPrecio` y este módulo solo calcula. El total se cuantiza
a centavos (core.money); FerreBot redondeaba a pesos enteros (desviación deliberada, G2).
"""
from dataclasses import dataclass, field
from decimal import Decimal

from core.money import cuantizar

# Tolerancia para casar la cantidad con el decimal de una fracción (1/4 == 0.25, etc.).
_TOLERANCIA_FRACCION = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class FraccionPrecio:
    """Una fila de productos_fracciones: el decimal de la fracción y su precio total."""
    decimal: Decimal | None
    precio_total: Decimal


@dataclass(frozen=True, slots=True)
class EsquemaPrecio:
    """Los tres esquemas de precio de un producto (los que no aplican van en None/vacío)."""
    precio_venta: Decimal
    precio_umbral: Decimal | None = None
    precio_bajo_umbral: Decimal | None = None
    precio_sobre_umbral: Decimal | None = None
    fracciones: tuple[FraccionPrecio, ...] = field(default_factory=tuple)

    @property
    def tiene_escalonado(self) -> bool:
        return (
            self.precio_umbral is not None
            and self.precio_bajo_umbral is not None
            and self.precio_sobre_umbral is not None
        )


def _fraccion_que_coincide(esquema: EsquemaPrecio, cantidad: Decimal) -> FraccionPrecio | None:
    for fraccion in esquema.fracciones:
        if fraccion.decimal is not None and abs(fraccion.decimal - cantidad) < _TOLERANCIA_FRACCION:
            return fraccion
    return None


def obtener_precio_para_cantidad(
    esquema: EsquemaPrecio, cantidad: Decimal
) -> tuple[Decimal, Decimal]:
    """Devuelve (total_linea, precio_unitario) aplicando el primer esquema que corresponda."""
    if esquema.tiene_escalonado:
        precio_unitario = (
            esquema.precio_sobre_umbral
            if cantidad >= esquema.precio_umbral
            else esquema.precio_bajo_umbral
        )
        return cuantizar(precio_unitario * cantidad), precio_unitario

    fraccion = _fraccion_que_coincide(esquema, cantidad)
    if fraccion is not None:
        return cuantizar(fraccion.precio_total), esquema.precio_venta

    return cuantizar(esquema.precio_venta * cantidad), esquema.precio_venta


def regla_para_cantidad(esquema: EsquemaPrecio, cantidad: Decimal) -> str:
    """Etiqueta del esquema aplicado: 'escalonado' | 'fraccion' | 'simple' (para el API)."""
    if esquema.tiene_escalonado:
        return "escalonado"
    if _fraccion_que_coincide(esquema, cantidad) is not None:
        return "fraccion"
    return "simple"
