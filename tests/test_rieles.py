"""Rieles de validación de voz (ADR 0005): decisión pura, sin BD.

Cubre los tres rieles y el umbral de precio (pct con piso en pesos), incluyendo los cortes
exactos que importan: producto desconocido vs ambiguo, precio declarado que NO se cuestiona, y
la confirmación que solo aplica cuando la empresa la exige y el usuario aún no dijo «sí».
"""
from decimal import Decimal

from ai.rieles import (
    Confirmar,
    Ejecutar,
    ItemPrecio,
    ItemResuelto,
    Preguntar,
    precio_dudoso,
    riel_confirmacion,
    riel_precio,
    riel_producto,
)

PCT = Decimal("1")      # 1 %
MIN = Decimal("1")      # mínimo 1 peso


# --- Riel 1: producto --------------------------------------------------------
def test_producto_unico_ejecuta():
    assert isinstance(riel_producto([ItemResuelto("cemento gris", ("Cemento gris 50kg",))]), Ejecutar)


def test_producto_cero_candidatos_pregunta_no_encontrado():
    d = riel_producto([ItemResuelto("xyz", ())])
    assert isinstance(d, Preguntar) and d.codigo == "producto_no_encontrado"


def test_producto_varios_candidatos_pregunta_ambiguo():
    d = riel_producto([ItemResuelto("cemento", ("Cemento gris", "Cemento blanco", "Cemento x"))])
    assert isinstance(d, Preguntar) and d.codigo == "producto_ambiguo"


def test_producto_corta_en_el_primero_que_falla():
    d = riel_producto([
        ItemResuelto("ok", ("Producto ok",)),
        ItemResuelto("nada", ()),
        ItemResuelto("varios", ("A", "B")),
    ])
    assert isinstance(d, Preguntar) and d.codigo == "producto_no_encontrado"


# --- Riel 2: precio dudoso ---------------------------------------------------
def test_precio_dentro_de_tolerancia_no_es_dudoso():
    # 28000 catálogo, 1 % = 280; 28200 está dentro.
    assert precio_dudoso(Decimal("28200"), Decimal("28000"), tolerancia_pct=PCT, tolerancia_min=MIN) is False


def test_precio_fuera_de_tolerancia_es_dudoso():
    assert precio_dudoso(Decimal("31000"), Decimal("28000"), tolerancia_pct=PCT, tolerancia_min=MIN) is True


def test_precio_piso_minimo_en_pesos_para_montos_chicos():
    # catálogo 50: 1 % = 0.5, pero el piso es 1 peso → 51 (diff 1) NO es dudoso; 52 (diff 2) sí.
    assert precio_dudoso(Decimal("51"), Decimal("50"), tolerancia_pct=PCT, tolerancia_min=MIN) is False
    assert precio_dudoso(Decimal("52"), Decimal("50"), tolerancia_pct=PCT, tolerancia_min=MIN) is True


def test_riel_precio_declarado_no_se_cuestiona():
    # El usuario dijo el precio (declarado=True): aunque difiera del catálogo, ejecuta.
    item = ItemPrecio("martillo", Decimal("25000"), Decimal("11900"), declarado=True)
    assert isinstance(riel_precio([item], tolerancia_pct=PCT, tolerancia_min=MIN), Ejecutar)


def test_riel_precio_no_declarado_y_difiere_pregunta():
    item = ItemPrecio("martillo", Decimal("25000"), Decimal("11900"), declarado=False)
    d = riel_precio([item], tolerancia_pct=PCT, tolerancia_min=MIN)
    assert isinstance(d, Preguntar) and d.codigo == "precio_dudoso"


# --- Riel 3: confirmación ----------------------------------------------------
def test_confirmacion_requerida_sin_confirmar_corta():
    d = riel_confirmacion(requiere=True, confirmado=False, resumen="Gasto $15.000. ¿Confirmo?")
    assert isinstance(d, Confirmar) and "Confirmo" in d.resumen


def test_confirmacion_ya_confirmada_ejecuta():
    assert isinstance(riel_confirmacion(requiere=True, confirmado=True, resumen="x"), Ejecutar)


def test_confirmacion_no_requerida_ejecuta():
    assert isinstance(riel_confirmacion(requiere=False, confirmado=False, resumen="x"), Ejecutar)
