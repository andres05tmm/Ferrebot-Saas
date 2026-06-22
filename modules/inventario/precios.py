"""Motor de precios — port de `catalogo_service.py:315` (ferrebot-logica-portar.md §3).

Función pura `obtener_precio_para_cantidad`: dado el esquema de precio de un producto y una
cantidad, devuelve (total_de_la_linea, precio_unitario). Cuatro esquemas que conviven, evaluados
SIEMPRE en este orden:

    1. Escalonado por umbral  → precio_sobre_umbral si cantidad >= umbral, si no precio_bajo_umbral.
    2. Por fracción           → si alguna fracción coincide (|decimal - cantidad| < 0.01).
    3. Sub-unidad (granel)    → si `unidad_medida` se vende por sub-unidad (gramo/cm), `precio_venta`
                                es el precio del PAQUETE y la cantidad viene en la sub-unidad:
                                total = precio_venta * cantidad / unidades_por_paquete.
    4. Simple                 → precio_venta * cantidad.

Sin SQL: el repositorio arma el `EsquemaPrecio` y este módulo solo calcula. El total se cuantiza
a centavos (core.money); FerreBot redondeaba a pesos enteros (desviación deliberada, G2).
"""
from dataclasses import dataclass, field
from decimal import Decimal

from core.money import cuantizar

# Tolerancia para casar la cantidad con el decimal de una fracción (1/4 == 0.25, etc.).
_TOLERANCIA_FRACCION = Decimal("0.01")

# Productos por sub-unidad: `unidad_medida` (DATA del catálogo) → unidades por paquete. El
# `precio_venta` de estos productos es el precio del PAQUETE COMPLETO y la cantidad de la venta viene
# expresada en la sub-unidad (gramos, centímetros), no en paquetes. Así el menudeo cobra exacto:
#   GRM/Gramos: puntillas — la caja trae 500 gramos; `precio_venta` = precio de la caja (puntorojo).
#   Cms:        lija esmeril — se cobra por cm; `precio_venta` está expresado por 100 cm.
# El 500 está portado de `bot-ventas-ferreteria/bypass.py` (`_PESO_CAJA_GR`); el 100 (cm) es la
# convención del negocio confirmada por el owner. Universal (no por-tenant): la señal por-producto la
# da `unidad_medida`; estas son convenciones del oficio. Normalización lo case-insensitiviza.
_UNIDADES_POR_PAQUETE: dict[str, Decimal] = {
    "grm": Decimal("500"),
    "gramos": Decimal("500"),
    "cms": Decimal("100"),
}


def unidades_por_paquete(unidad_medida: str | None) -> Decimal | None:
    """Unidades por paquete si el producto se vende por sub-unidad (gramo/cm); None si no aplica."""
    if not unidad_medida:
        return None
    return _UNIDADES_POR_PAQUETE.get(unidad_medida.strip().lower())


@dataclass(frozen=True, slots=True)
class FraccionPrecio:
    """Una fila de productos_fracciones: el decimal de la fracción y su precio total."""
    decimal: Decimal | None
    precio_total: Decimal


@dataclass(frozen=True, slots=True)
class EsquemaPrecio:
    """Los esquemas de precio de un producto (los que no aplican van en None/vacío/default).

    `unidad_medida` es la unidad de venta del catálogo: "Unidad" (default) para el caso normal, o una
    sub-unidad de granel ("GRM"/"Cms") que activa el esquema 3 (ver `obtener_precio_para_cantidad`).
    """
    precio_venta: Decimal
    precio_umbral: Decimal | None = None
    precio_bajo_umbral: Decimal | None = None
    precio_sobre_umbral: Decimal | None = None
    fracciones: tuple[FraccionPrecio, ...] = field(default_factory=tuple)
    unidad_medida: str = "Unidad"

    @property
    def tiene_escalonado(self) -> bool:
        return (
            self.precio_umbral is not None
            and self.precio_bajo_umbral is not None
            and self.precio_sobre_umbral is not None
        )

    @property
    def unidades_por_paquete(self) -> Decimal | None:
        """Unidades por paquete si se vende por sub-unidad (granel: gramo/cm); None si no aplica."""
        return unidades_por_paquete(self.unidad_medida)


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

    # Granel: `precio_venta` es el precio del paquete; la cantidad viene en la sub-unidad. El precio
    # por sub-unidad (precio_venta/unidades) NO se cuantiza —es informativo y de baja magnitud—; el
    # total sí. "500 puntilla" (GRM, caja=500g) → 7500*500/500 = 7500; "30 lija esmeril" (Cms) → /100.
    unidades = esquema.unidades_por_paquete
    if unidades is not None and unidades > 0:
        return (
            cuantizar(esquema.precio_venta * cantidad / unidades),
            esquema.precio_venta / unidades,
        )

    return cuantizar(esquema.precio_venta * cantidad), esquema.precio_venta


def regla_para_cantidad(esquema: EsquemaPrecio, cantidad: Decimal) -> str:
    """Etiqueta del esquema: 'escalonado' | 'fraccion' | 'subunidad' | 'simple' (para el API)."""
    if esquema.tiene_escalonado:
        return "escalonado"
    if _fraccion_que_coincide(esquema, cantidad) is not None:
        return "fraccion"
    if esquema.unidades_por_paquete is not None:
        return "subunidad"
    return "simple"
