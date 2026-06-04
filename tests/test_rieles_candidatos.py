"""Entregable 2 — corrección del checkpoint: `producto_ambiguo` debe LISTAR los candidatos.

Hoy `ItemResuelto` solo lleva un conteo (`candidatos: int`), así que el corte ambiguo dice
"¿cuál de ellos?" sin mostrar cuáles. Estos tests fijan la nueva forma: `ItemResuelto` lleva los
NOMBRES de los candidatos y `riel_producto` los enumera en el mensaje (van directo al usuario, sin
ronda de modelo — por eso el mensaje tiene que ser autosuficiente).
"""
from ai.rieles import Ejecutar, ItemResuelto, Preguntar, riel_producto


def test_ambiguo_lista_los_nombres_de_candidatos():
    items = [ItemResuelto("cemento", ("Cemento gris 50kg", "Cemento blanco 25kg"))]
    d = riel_producto(items)
    assert isinstance(d, Preguntar)
    assert d.codigo == "producto_ambiguo"
    assert "Cemento gris 50kg" in d.mensaje
    assert "Cemento blanco 25kg" in d.mensaje


def test_no_encontrado_nombra_la_referencia():
    d = riel_producto([ItemResuelto("taladro percutor", ())])
    assert isinstance(d, Preguntar)
    assert d.codigo == "producto_no_encontrado"
    assert "taladro percutor" in d.mensaje


def test_candidato_unico_ejecuta():
    assert isinstance(riel_producto([ItemResuelto("martillo", ("Martillo Truper",))]), Ejecutar)


def test_corta_en_el_primer_item_problematico():
    items = [
        ItemResuelto("martillo", ("Martillo Truper",)),      # ok
        ItemResuelto("tornillo", ()),                          # no encontrado → corta aquí
    ]
    d = riel_producto(items)
    assert isinstance(d, Preguntar)
    assert d.codigo == "producto_no_encontrado"
    assert "tornillo" in d.mensaje
