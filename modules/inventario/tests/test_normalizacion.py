"""Normalizador universal de términos (typos/abreviaturas del oficio, multi-tenant)."""
import pytest

from modules.inventario.normalizacion import normalizar_terminos


@pytest.mark.parametrize("entrada,esperado", [
    # disolventes
    ("2 tiner", "2 thinner"),
    ("1 galon de tinner", "1 galon de thinner"),
    ("varsol", "varsol"),
    ("1 barsol", "1 varsol"),
    ("bar sol", "varsol"),
    # (los typos de wayper se prueban junto con la resolución kilo/unidad más abajo)
    # drywall (familia de typos)
    ("24 tornillos drwall 6x1", "24 tornillos drywall 6x1"),
    ("draigual", "drywall"),
    ("driwall", "drywall"),
    # herrajes
    ("60 tornillos tira fondo", "60 tornillos tirafondo"),
    ("2 rodachines", "2 rodachina"),
    ("3en1", "3 en 1"),
])
def test_alias_universal(entrada, esperado):
    assert normalizar_terminos(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    ("puntilla 2 s.c.", "puntilla 2 sin cabeza"),
    ("puntilla 1 c.c", "puntilla 1 con cabeza"),
    ("puntilla 2 sc", "puntilla 2 sin cabeza"),
    ("puntilla 1 cc", "puntilla 1 con cabeza"),
    ("vinilo t-1", "vinilo t1"),
    ("vinilo t-2 blanco", "vinilo t2 blanco"),
])
def test_abreviaturas(entrada, esperado):
    assert normalizar_terminos(entrada) == esperado


def test_sc_solo_aplica_con_puntilla_presente():
    # "sc"/"cc" sueltas sin "puntilla" NO se tocan (evita pisar siglas no relacionadas).
    assert normalizar_terminos("tornillo sc") == "tornillo sc"
    # con "puntilla" sí.
    assert normalizar_terminos("caja puntilla sc") == "caja puntilla sin cabeza"


def test_normalizador_no_toca_de_antes_de_medida():
    # El "de" antes de una medida ("tornillo drywall de 6x1") NO lo quita el normalizador —lo intenta
    # el bypass como reintento de resolución—, para no romper nombres que SÍ llevan "de N".
    assert normalizar_terminos("tornillos drywall de 6x1") == "tornillos drywall de 6x1"


def test_typo_wayper_se_corrige_sin_desambiguar_kilo_unidad():
    # El typo se corrige (waype→wayper); la desambiguación kilo/unidad del pelado queda al match
    # exacto/modelo (pendiente de decisión del owner), por eso NO se añade "unidad" aquí.
    assert normalizar_terminos("2 waype blanco") == "2 wayper blanco"
    assert normalizar_terminos("3 waiper de color") == "3 wayper de color"
    assert normalizar_terminos("guayper") == "wayper"


def test_no_toca_terminos_correctos():
    # Un material ya canónico o un producto sin typo no cambia.
    assert normalizar_terminos("1 galon de thinner") == "1 galon de thinner"
    assert normalizar_terminos("2 tornillo drywall 6x2") == "2 tornillo drywall 6x2"
    assert normalizar_terminos("vinilo davinci t1 blanco") == "vinilo davinci t1 blanco"


def test_vacio_y_espacios():
    assert normalizar_terminos("") == ""
    assert normalizar_terminos("   ") == ""
