"""Catálogo de capacidades (feature-flags.md): fuente única + validación de dependencias.

Módulo PURO (sin IO/DB). Se cubren: núcleo siempre presente en `capacidades_completas`; dependencias
en modo OR (basta una del conjunto requisito); y `es_feature_valida` contra NUCLEO ∪ OPCIONALES.
"""
from core.tenancy.catalogo import (
    NUCLEO,
    OPCIONALES,
    capacidades_completas,
    es_feature_valida,
    validar_dependencias,
)


def test_nucleo_siempre_en_capacidades_completas():
    completas = capacidades_completas(frozenset())
    assert NUCLEO <= completas
    assert completas == NUCLEO          # sin efectivas, queda solo el núcleo


def test_efectivas_se_unen_al_nucleo():
    completas = capacidades_completas(frozenset({"fiados"}))
    assert NUCLEO <= completas
    assert "fiados" in completas


def test_conjunto_sin_opcionales_sin_errores():
    assert validar_dependencias(frozenset()) == []
    assert validar_dependencias(NUCLEO) == []


def test_notas_sin_facturacion_error():
    errores = validar_dependencias(frozenset({"notas_electronicas"}))
    assert errores != []


def test_notas_con_facturacion_ok():
    assert validar_dependencias(frozenset({"facturacion_electronica", "notas_electronicas"})) == []


def test_libro_iva_valido_con_compras_fiscal_sin_facturacion():
    # libro_iva depende de {facturacion_electronica, compras_fiscal} en OR: basta compras_fiscal.
    assert validar_dependencias(frozenset({"compras_fiscal", "libro_iva"})) == []


def test_libro_iva_sin_ninguna_dependencia_error():
    assert validar_dependencias(frozenset({"libro_iva"})) != []


def test_ventas_voz_sin_bot_error():
    assert validar_dependencias(frozenset({"ventas_voz"})) != []


def test_ventas_voz_con_bot_ok():
    assert validar_dependencias(frozenset({"bot_telegram", "ventas_voz"})) == []


# --- pack `pos` (ADR 0008): retail dejó de ser núcleo ---------------------

def test_pos_es_feature_valida_opcional():
    assert es_feature_valida("pos") is True
    assert "pos" in OPCIONALES


def test_pos_no_esta_en_nucleo_solo_clientes_y_reportes():
    # El POS salió del núcleo; solo queda lo transversal (ADR 0008 §D2).
    assert NUCLEO == frozenset({"clientes", "reportes"})
    for retail in ("ventas", "inventario", "caja", "gastos", "proveedores"):
        assert es_feature_valida(retail) is False         # ya no son features (las agrupa `pos`)


def test_fiados_requiere_pos():
    assert validar_dependencias(frozenset({"fiados"})) != []            # sin pos → error
    assert validar_dependencias(frozenset({"pos", "fiados"})) == []     # con pos → ok


def test_mayorista_requiere_pos():
    assert validar_dependencias(frozenset({"mayorista"})) != []
    assert validar_dependencias(frozenset({"pos", "mayorista"})) == []


def test_es_feature_valida():
    assert es_feature_valida("clientes") is True          # núcleo (transversal)
    assert es_feature_valida("pos") is True               # opcional (pack retail)
    assert es_feature_valida("facturacion_electronica") is True  # opcional
    assert es_feature_valida("inexistente") is False
    assert OPCIONALES.isdisjoint(NUCLEO)                  # sin solapamiento
