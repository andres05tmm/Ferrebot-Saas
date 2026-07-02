"""Catálogo de capacidades (feature-flags.md): fuente única + validación de dependencias.

Módulo PURO (sin IO/DB). Se cubren: núcleo siempre presente en `capacidades_completas`; dependencias
en modo OR (basta una del conjunto requisito); `es_feature_valida` contra NUCLEO ∪ OPCIONALES; y la
expansión del meta-pack `pos` → {ventas, caja, inventario} (partición del pack, ADR).
"""
from core.tenancy.catalogo import (
    META_PACKS,
    NUCLEO,
    OPCIONALES,
    capacidades_completas,
    es_feature_valida,
    expandir_metapacks,
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
    # Partición del pack: ventas/caja/inventario son features finas válidas; el resto sigue agrupado.
    for fina in ("ventas", "caja", "inventario"):
        assert es_feature_valida(fina) is True
        assert fina in OPCIONALES
    for agrupada in ("gastos", "compras", "proveedores"):
        assert es_feature_valida(agrupada) is False       # viven dentro de caja/inventario


def test_fiados_requiere_pos():
    assert validar_dependencias(frozenset({"fiados"})) != []            # sin ventas → error
    assert validar_dependencias(frozenset({"pos", "fiados"})) == []     # pos expande a ventas → ok


def test_mayorista_requiere_pos():
    assert validar_dependencias(frozenset({"mayorista"})) != []
    assert validar_dependencias(frozenset({"pos", "mayorista"})) == []


# --- partición del pack `pos`: meta-pack + features finas ------------------

def test_metapack_pos_declara_las_tres_finas():
    assert META_PACKS["pos"] == frozenset({"ventas", "caja", "inventario"})


def test_expandir_metapacks_conserva_el_flag_meta():
    expandido = expandir_metapacks(frozenset({"pos"}))
    assert expandido == frozenset({"pos", "ventas", "caja", "inventario"})


def test_expandir_metapacks_es_idempotente():
    una_vez = expandir_metapacks(frozenset({"pos", "fiados"}))
    assert expandir_metapacks(una_vez) == una_vez


def test_expandir_metapacks_sin_meta_es_noop():
    features = frozenset({"caja", "ventas", "pack_agenda"})
    assert expandir_metapacks(features) == features


def test_capacidades_completas_expande_el_metapack():
    completas = capacidades_completas(frozenset({"pos"}))
    assert frozenset({"pos", "ventas", "caja", "inventario"}) <= completas
    assert NUCLEO <= completas


def test_fiados_con_ventas_fina_ok():
    # Una peluquería con `ventas` (sin pos) puede fiar: la dependencia es la feature fina.
    assert validar_dependencias(frozenset({"ventas", "fiados"})) == []


def test_inventario_requiere_ventas():
    # El stock es DE productos del catálogo (que vive tras `ventas`).
    assert validar_dependencias(frozenset({"inventario"})) != []
    assert validar_dependencias(frozenset({"ventas", "inventario"})) == []


def test_packs_dependientes_aceptan_la_fina_o_el_metapack():
    # pack_pedidos/pack_ventas cotizan/venden el catálogo → requieren `ventas` (pos lo satisface).
    for pack in ("pack_pedidos", "pack_ventas"):
        assert validar_dependencias(frozenset({pack})) != []
        assert validar_dependencias(frozenset({"ventas", pack})) == []
        assert validar_dependencias(frozenset({"pos", pack})) == []


def test_pack_pagar_requiere_inventario():
    # Su fuente (facturas_proveedores) la escribe el módulo proveedores, que vive tras `inventario`.
    assert validar_dependencias(frozenset({"pack_pagar"})) != []
    assert validar_dependencias(frozenset({"ventas", "inventario", "pack_pagar"})) == []
    assert validar_dependencias(frozenset({"pos", "pack_pagar"})) == []


def test_caja_sola_es_valida_sin_dependencias():
    # `caja` no depende de `ventas`: el arqueo degrada a 0 ventas_efectivo.
    assert validar_dependencias(frozenset({"caja"})) == []


def test_es_feature_valida():
    assert es_feature_valida("clientes") is True          # núcleo (transversal)
    assert es_feature_valida("pos") is True               # opcional (pack retail)
    assert es_feature_valida("facturacion_electronica") is True  # opcional
    assert es_feature_valida("inexistente") is False
    assert OPCIONALES.isdisjoint(NUCLEO)                  # sin solapamiento
