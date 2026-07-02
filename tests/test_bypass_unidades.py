"""Bypass — resolución de productos con unidades/plurales (port de bypass.py viejo, adaptado).

El bug: "2 galones de thinner" pasaba "galones de thinner" a la capa exacta (estricta) → 0 matches.
Fix: normalizar plurales en el slug y, si el match falla, reintentar quitando la unidad inicial
("galón/galones/kilo/...", opcionalmente seguida de "de"). El reintento solo cuenta si da match único
(lo garantiza `producto_exacto`, que devuelve None ante 0 o >1). Aplica en la resolución compartida
(`_resolver_match`), así que sirve para `intentar` y `preparar`.
"""
from decimal import Decimal
from types import SimpleNamespace

from ai.bypass import (
    Bypass,
    ProductoBypass,
    normalizar_slug,
    quitar_de_medida,
    quitar_unidad_inicial,
)
from ai.envelope import Contexto
from modules.inventario.precios import EsquemaPrecio, FraccionPrecio


class _FakeCatalogo:
    """CatalogoBypass falso: resuelve por slug EXACTO (dict) y registra los slugs consultados."""

    def __init__(self, productos):
        self._p = dict(productos)
        self.consultados: list[str] = []

    async def producto_exacto(self, slug):
        self.consultados.append(slug)
        return self._p.get(slug)


def _prod(id_, nombre, precio="1000", fracciones=()):
    return ProductoBypass(
        id=id_, nombre=nombre,
        esquema=EsquemaPrecio(precio_venta=Decimal(precio), fracciones=tuple(fracciones)),
    )


def _ctx():
    return Contexto(tenant_id=1, usuario_id=1, rol="vendedor", origen="bot",
                    idempotency_key="k", capacidades=frozenset({"ventas"}))


def _recursos():
    return SimpleNamespace(resueltos={})


# --------------------------- helpers puros --------------------------------

def test_normalizar_slug_singulariza_plurales():
    assert normalizar_slug("galones") == "galon"
    assert normalizar_slug("tornillos") == "tornillo"
    assert normalizar_slug("puntillas") == "puntilla"


def test_normalizar_slug_no_toca_nombre_compuesto_sin_plural():
    # No debe mutar un nombre que no lleva plurales (no romper "vinilo davinci t1 blanco").
    assert normalizar_slug("vinilo davinci t1 blanco") == "vinilo davinci t1 blanco"


def test_quitar_unidad_inicial_quita_unidad_y_de_opcional():
    assert quitar_unidad_inicial("galon de thinner") == "thinner"
    assert quitar_unidad_inicial("galon thinner") == "thinner"
    assert quitar_unidad_inicial("kilo de cemento") == "cemento"
    assert quitar_unidad_inicial("litro pintura") == "pintura"


def test_quitar_unidad_inicial_no_toca_productos_que_no_empiezan_con_unidad():
    assert quitar_unidad_inicial("vinilo davinci t1 blanco") == "vinilo davinci t1 blanco"
    assert quitar_unidad_inicial("thinner") == "thinner"


# --------------------------- resolución end-to-end (preparar) -------------

async def test_preparar_resuelve_galones_de_thinner_qty_2():
    cat = _FakeCatalogo({"thinner": _prod(7, "thinner")})
    prep = await Bypass(cat, dispatcher=None).preparar("2 galones de thinner", _ctx(), _recursos())
    assert prep is not None
    assert prep.tool_call.arguments["items"] == [{"producto_id": 7, "cantidad": Decimal("2")}]
    assert cat.consultados[-1] == "thinner"        # reintentó quitando la unidad


async def test_preparar_resuelve_medio_galon_de_thinner_qty_media():
    # El thinner se vende por fracción (1/2): el bypass resuelve la media unidad (no inventa precio).
    thinner = _prod(7, "thinner",
                    fracciones=[FraccionPrecio(decimal=Decimal("0.5"), precio_total=Decimal("500"))])
    cat = _FakeCatalogo({"thinner": thinner})
    prep = await Bypass(cat, dispatcher=None).preparar("medio galón de thinner", _ctx(), _recursos())
    assert prep is not None
    assert prep.tool_call.arguments["items"] == [{"producto_id": 7, "cantidad": Decimal("0.5")}]


async def test_preparar_no_reintenta_si_el_nombre_completo_matchea():
    # "vinilo davinci t1 blanco" debe resolver con el slug COMPLETO; no se quita ninguna palabra.
    cat = _FakeCatalogo({"vinilo davinci t1 blanco": _prod(3, "vinilo davinci t1 blanco")})
    prep = await Bypass(cat, dispatcher=None).preparar(
        "2 vinilo davinci t1 blanco", _ctx(), _recursos()
    )
    assert prep is not None
    assert prep.tool_call.arguments["items"][0]["producto_id"] == 3
    assert cat.consultados == ["vinilo davinci t1 blanco"]   # un solo intento (sin quitar unidad)


async def test_preparar_singulariza_tornillos():
    cat = _FakeCatalogo({"tornillo": _prod(9, "tornillo")})
    prep = await Bypass(cat, dispatcher=None).preparar("3 tornillos", _ctx(), _recursos())
    assert prep is not None
    assert prep.tool_call.arguments["items"] == [{"producto_id": 9, "cantidad": Decimal("3")}]


# --------------------------- "de" antes de medida + typos ------------------

def test_quitar_de_medida_solo_antes_de_numero():
    assert quitar_de_medida("tornillo drywall de 6x1") == "tornillo drywall 6x1"
    assert quitar_de_medida("chazo de 3 8") == "chazo 3 8"
    # "de <palabra>" no se toca (eso lo maneja quitar_unidad_inicial).
    assert quitar_de_medida("galon de thinner") == "galon de thinner"


async def test_preparar_reintenta_quitando_de_antes_de_medida():
    # El vendedor dice "tornillos drywall de 6x1"; el catálogo es "tornillo drywall 6x1".
    cat = _FakeCatalogo({"tornillo drywall 6x1": _prod(40, "tornillo drywall 6x1")})
    prep = await Bypass(cat, dispatcher=None).preparar("3 tornillos drywall de 6x1", _ctx(), _recursos())
    assert prep is not None
    assert prep.tool_call.arguments["items"][0]["producto_id"] == 40
    assert cat.consultados[-1] == "tornillo drywall 6x1"   # reintentó sin "de"


async def test_preparar_nombre_con_de_medida_resuelve_directo_sin_romper():
    # Un producto cuyo NOMBRE sí lleva "de N" matchea con el slug original (el reintento no lo rompe).
    cat = _FakeCatalogo({"tornillo de 5 16": _prod(41, "tornillo de 5 16")})
    prep = await Bypass(cat, dispatcher=None).preparar("4 tornillo de 5/16", _ctx(), _recursos())
    assert prep is not None
    assert prep.tool_call.arguments["items"][0]["producto_id"] == 41
    assert cat.consultados[0] == "tornillo de 5 16"        # casó al primer intento


async def test_preparar_corrige_typo_universal_tiner_thinner():
    cat = _FakeCatalogo({"thinner": _prod(42, "thinner")})
    prep = await Bypass(cat, dispatcher=None).preparar("2 tiner", _ctx(), _recursos())
    assert prep is not None
    assert prep.tool_call.arguments["items"][0]["producto_id"] == 42
