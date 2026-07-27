"""Motor de precios — port de `catalogo_service.py:315` (ferrebot-logica-portar.md §3).

Función pura `obtener_precio_para_cantidad`: dado el esquema de precio de un producto y una
cantidad, devuelve (total_de_la_linea, precio_unitario). La cantidad viene SIEMPRE en la unidad de
venta (un kilo, un gramo, una unidad) — es la única unidad que existe en inventario, kárdex y
factura. Los esquemas se evalúan SIEMPRE en este orden:

    1. Empaque entero (`por_paquete`) → la bolsa de cemento a precio fijo: la cantidad se lee como
                                        `cantidad / contenido_paquete` empaques y cada uno cuesta
                                        `precio_paquete`. Es explícito: lo pide quien vende, nunca
                                        se adivina (nadie quiere un descuento sorpresa por llevar
                                        justo 50 kg sueltos).
    2. Escalonado por umbral  → precio_sobre_umbral si cantidad >= umbral, si no precio_bajo_umbral.
    3. Por fracción           → si alguna fracción coincide (|decimal - cantidad| < 0.01).
    4. Simple                 → precio_venta * cantidad.

`precio_venta` significa SIEMPRE el precio de UNA unidad de venta (0072). Antes había una quinta
rama —"sub-unidad"— donde para el granel (GRM/Cms/MLT) `precio_venta` era el precio del PAQUETE y el
motor dividía por una convención clavada en el código. Eso hacía que el mismo campo significara dos
cosas según la unidad de medida, y volvía imposible tener a la vez el precio del bulto y el del kilo
suelto. Ahora son dos datos distintos y el motor no adivina nada.

Sin SQL: el repositorio arma el `EsquemaPrecio` y este módulo solo calcula. El total se cuantiza
a centavos (core.money); FerreBot redondeaba a pesos enteros (desviación deliberada, G2).
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from core.money import cuantizar

# Tolerancia para casar la cantidad con el decimal de una fracción (1/4 == 0.25, etc.).
_TOLERANCIA_FRACCION = Decimal("0.01")

# Tamaño del empaque cuando el producto no lo trae como dato: convención del oficio ferretero por
# `unidad_medida`. Ya NO decide precios (0072) — solo dice cuántas unidades de venta trae el empaque,
# que es lo que necesitan la captura de compras/conteos y el cobro del empaque entero:
#   GRM/Gramos: puntillas — la caja trae 500 gramos.
#   Cms:        lija esmeril — el rollo trae 100 cm.
#   MLT/ML:     tintillas — el tarro trae 1000 ml (1 L).
# El 500 está portado de `bot-ventas-ferreteria/bypass.py` (`_PESO_CAJA_GR`); el 100 (cm) y el 1000
# (ml, ported de `ModalMlt`) son convenciones del negocio confirmadas por el owner. Manda siempre el
# DATO del producto (`contenido_paquete`, 0069): esto es solo el respaldo para lo que no lo tenga.
_UNIDADES_POR_PAQUETE: dict[str, Decimal] = {
    "grm": Decimal("500"),
    "gramos": Decimal("500"),
    "cms": Decimal("100"),
    "mlt": Decimal("1000"),
    "ml": Decimal("1000"),
    "mililitros": Decimal("1000"),
}


def unidades_por_paquete(
    unidad_medida: str | None, contenido_paquete: Decimal | None = None
) -> Decimal | None:
    """Cuántas unidades de venta trae el empaque con que se compra el producto.

    Manda el DATO del producto (`contenido_paquete`, 0069: la bolsa de cal trae 25 kg, el bulto de
    cemento 50); si no lo tiene, aplica la convención por unidad de medida (caja de puntilla = 500 g,
    rollo de lija = 100 cm, tarro de tinte = 1000 ml). None = el producto no se compra por empaque.
    """
    if contenido_paquete is not None and contenido_paquete > 0:
        return Decimal(contenido_paquete)
    if not unidad_medida:
        return None
    return _UNIDADES_POR_PAQUETE.get(unidad_medida.strip().lower())


# Unidad en la que se CAPTURA una cantidad de un producto granel: la sub-unidad en la que vive el
# stock (gramo/cm/ml) o el paquete con el que se compra y se cuenta (la caja, el tarro, el rollo).
UnidadCaptura = Literal["sub", "paquete"]


def convertir_a_subunidad(
    cantidad: Decimal,
    costo: Decimal | None,
    *,
    unidad: str,
    unidad_medida: str | None,
    contenido_paquete: Decimal | None = None,
) -> tuple[Decimal, Decimal | None]:
    """Pasa una captura en PAQUETES a la unidad de venta (y su costo).

    La ferretería compra cajas de puntilla y bolsas de cal, pero vende gramos y kilos: si no se
    convierte, "10 cajas" suma 10 gramos y "2 bolsas" suma 2 kilos, con el costo en $/empaque contra
    un COGS en $/unidad. Función pura; sin empaque (o con la captura ya en la unidad de venta)
    devuelve los mismos números.
    """
    if unidad != "paquete":
        return cantidad, costo
    factor = unidades_por_paquete(unidad_medida, contenido_paquete)
    if factor is None or factor <= 0:
        return cantidad, costo
    return cantidad * factor, (costo / factor if costo is not None else None)


@dataclass(frozen=True, slots=True)
class FraccionPrecio:
    """Una fila de productos_fracciones: el decimal de la fracción y su precio total."""
    decimal: Decimal | None
    precio_total: Decimal


@dataclass(frozen=True, slots=True)
class EsquemaPrecio:
    """Los esquemas de precio de un producto (los que no aplican van en None/vacío/default).

    `precio_venta` es el precio de UNA unidad de venta; `precio_paquete` el del empaque completo
    (None = ese producto no se vende por empaque). `unidad_medida` ya no decide precio: solo sirve
    de respaldo para saber el tamaño del empaque cuando el producto no lo trae como dato.
    """
    precio_venta: Decimal
    precio_umbral: Decimal | None = None
    precio_bajo_umbral: Decimal | None = None
    precio_sobre_umbral: Decimal | None = None
    fracciones: tuple[FraccionPrecio, ...] = field(default_factory=tuple)
    unidad_medida: str = "Unidad"
    contenido_paquete: Decimal | None = None
    precio_paquete: Decimal | None = None

    @property
    def tiene_escalonado(self) -> bool:
        return (
            self.precio_umbral is not None
            and self.precio_bajo_umbral is not None
            and self.precio_sobre_umbral is not None
        )

    @property
    def unidades_por_paquete(self) -> Decimal | None:
        """Cuántas unidades de venta trae el empaque (dato del producto, o convención); None si no hay."""
        return unidades_por_paquete(self.unidad_medida, self.contenido_paquete)

    @property
    def vende_por_empaque(self) -> bool:
        """¿Se puede vender el empaque entero? Exige las dos mitades: el tamaño y su precio."""
        unidades = self.unidades_por_paquete
        return self.precio_paquete is not None and unidades is not None and unidades > 0


def _fraccion_que_coincide(esquema: EsquemaPrecio, cantidad: Decimal) -> FraccionPrecio | None:
    for fraccion in esquema.fracciones:
        if fraccion.decimal is not None and abs(fraccion.decimal - cantidad) < _TOLERANCIA_FRACCION:
            return fraccion
    return None


def obtener_precio_para_cantidad(
    esquema: EsquemaPrecio, cantidad: Decimal, *, por_empaque: bool = False
) -> tuple[Decimal, Decimal]:
    """Devuelve (total_linea, precio_unitario) aplicando el primer esquema que corresponda.

    `cantidad` va siempre en la unidad de venta. Con `por_empaque` se cobra a precio de empaque:
    3 bultos de 50 kg entran como cantidad=150 y se cobran 3 × `precio_paquete`. El unitario que sale
    es el efectivo por unidad ($28.000/50 = $560), para que la línea de la factura cuadre al
    multiplicar. El caller valida que el producto vende por empaque y que la cantidad es múltiplo.
    """
    if por_empaque and esquema.vende_por_empaque:
        empaques = cantidad / esquema.unidades_por_paquete
        total = cuantizar(esquema.precio_paquete * empaques)
        return total, total / cantidad

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


def regla_para_cantidad(
    esquema: EsquemaPrecio, cantidad: Decimal, *, por_empaque: bool = False
) -> str:
    """Etiqueta del esquema: 'empaque' | 'escalonado' | 'fraccion' | 'simple' (para el API)."""
    if por_empaque and esquema.vende_por_empaque:
        return "empaque"
    if esquema.tiene_escalonado:
        return "escalonado"
    if _fraccion_que_coincide(esquema, cantidad) is not None:
        return "fraccion"
    return "simple"
