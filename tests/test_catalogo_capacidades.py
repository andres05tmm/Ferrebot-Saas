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


def test_es_feature_valida():
    assert es_feature_valida("ventas") is True            # núcleo
    assert es_feature_valida("facturacion_electronica") is True  # opcional
    assert es_feature_valida("inexistente") is False
    assert OPCIONALES.isdisjoint(NUCLEO)                  # sin solapamiento
