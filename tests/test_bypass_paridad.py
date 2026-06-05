"""Paridad del parser del bypass con FerreBot (ferrebot-logica-portar.md §2): parte PURA.

Normalización, mapa de fracciones (numéricas y escritas), descomposición de cantidad y
deshabilitadores. Sin BD. La paridad del camino convergente (bypass → dispatcher.ejecutar → mismo
efecto en VentaService que el directo) vive en `test_bypass_convergencia.py`.
"""
from decimal import Decimal

import pytest

from ai.bypass import CaeAlModelo, VentaSimple, analizar, normalizar_slug


@pytest.mark.parametrize(
    "texto, producto, componentes",
    [
        ("2 martillo", "martillo", (Decimal("2"),)),
        ("3 vinilo", "vinilo", (Decimal("3"),)),
        ("1/2 vinilo azul t1", "vinilo azul t1", (Decimal("0.5"),)),
        ("1/4 lija", "lija", (Decimal("0.25"),)),
        ("1-1/2 vinilo", "vinilo", (Decimal("1"), Decimal("0.5"))),
        ("1 1/2 vinilo", "vinilo", (Decimal("1"), Decimal("0.5"))),
        ("medio vinilo", "vinilo", (Decimal("0.5"),)),
        ("tres cuartos vinilo", "vinilo", (Decimal("0.75"),)),
        ("1 y medio vinilo", "vinilo", (Decimal("1"), Decimal("0.5"))),
    ],
)
def test_analiza_ventas_simples(texto, producto, componentes):
    res = analizar(texto)
    assert isinstance(res, VentaSimple)
    assert res.producto == producto
    assert res.componentes == componentes


@pytest.mark.parametrize(
    "texto",
    [
        "fiado 2 martillo",          # crédito
        "2 martillo a nombre de pedro",
        "abono 5000 a juan",
        "2 martillo para Juan",      # para <Nombre propio> (mayúscula en original)
        "cuanto vale el martillo",   # consulta
        "hay stock de vinilo",
        "cambia el precio del martillo",  # modificación
        "2 martillo, 3 puntillas",   # multi-producto
        "2 martillo\n3 puntillas",
        "martillo",                  # sin cantidad → ambiguo
        "hola",
        "",
    ],
)
def test_deshabilitadores_caen_al_modelo(texto):
    assert isinstance(analizar(texto), CaeAlModelo)


def test_para_minuscula_no_deshabilita():
    # "para reja" (sustantivo común, minúscula) no es un cliente: sigue siendo bypass.
    res = analizar("2 tornillo para madera")
    assert isinstance(res, VentaSimple)


@pytest.mark.parametrize(
    "crudo, slug",
    [
        ("Lija #120", "lija n120"),
        ("Martillo Truper", "martillo truper"),
        ("Válvula 1/2 Roja", "valvula 1 2 roja"),
        ("CAÑO ½", "cano 1 2"),   # ½ (U+00BD) se descompone NFKD en 1⁄2
    ],
)
def test_normalizar_slug(crudo, slug):
    assert normalizar_slug(crudo) == slug
