"""ETL de catálogo (tools.etl_productos): lógica PURA, sin bases de datos.

Cubre el mapeo producto viejo→nuevo (IVA, precios, NULLs), `permite_fraccion`, el parser de
fracciones y la regla de dedup (por codigo / por nombre). La parte de E/S (read-only en el origen,
carga transaccional en el destino) NO se prueba aquí: requiere Postgres.
"""
from decimal import Decimal

import pytest

from tools.etl_productos import (
    construir_plan,
    debe_omitir,
    mapear_fraccion,
    mapear_producto,
    parsear_fraccion,
)


def _producto_viejo(**overrides) -> dict:
    """Fila de `productos` del esquema viejo, con valores por defecto razonables."""
    base = {
        "id": 1,
        "clave": "CLV-1",
        "codigo": "ABC123",
        "nombre": "Cemento gris 50kg",
        "categoria": "Construcción",
        "precio_unidad": 25000,
        "unidad_medida": "Bulto",
        "activo": True,
        "tiene_iva": True,
        "porcentaje_iva": 19,
        "precio_umbral": 10,
        "precio_bajo_umbral": 24000,
        "precio_sobre_umbral": 25000,
    }
    base.update(overrides)
    return base


# --- mapeo producto viejo -> nuevo -------------------------------------------------------------


def test_mapeo_campos_directos_y_precios():
    nuevo = mapear_producto(_producto_viejo(), tiene_fracciones=False)
    assert nuevo["codigo"] == "ABC123"
    assert nuevo["nombre"] == "Cemento gris 50kg"
    assert nuevo["categoria"] == "Construcción"
    assert nuevo["unidad_medida"] == "Bulto"
    assert nuevo["activo"] is True
    # precio_venta = precio_unidad, como Decimal; umbrales directos a Decimal.
    assert nuevo["precio_venta"] == Decimal("25000")
    assert isinstance(nuevo["precio_venta"], Decimal)
    assert nuevo["precio_umbral"] == Decimal("10")
    assert nuevo["precio_bajo_umbral"] == Decimal("24000")
    assert nuevo["precio_sobre_umbral"] == Decimal("25000")


def test_mapeo_iva_con_tiene_iva():
    nuevo = mapear_producto(_producto_viejo(tiene_iva=True, porcentaje_iva=19), tiene_fracciones=False)
    assert nuevo["iva"] == 19


def test_mapeo_iva_sin_tiene_iva_es_cero():
    # Aunque haya porcentaje, si tiene_iva es False el IVA efectivo es 0.
    nuevo = mapear_producto(_producto_viejo(tiene_iva=False, porcentaje_iva=19), tiene_fracciones=False)
    assert nuevo["iva"] == 0


def test_mapeo_proveedor_compra_especial_son_null():
    nuevo = mapear_producto(_producto_viejo(), tiene_fracciones=False)
    assert nuevo["proveedor_id"] is None
    assert nuevo["precio_compra"] is None
    assert nuevo["precio_especial"] is None


def test_mapeo_umbrales_null_se_preservan():
    nuevo = mapear_producto(
        _producto_viejo(precio_umbral=None, precio_bajo_umbral=None, precio_sobre_umbral=None),
        tiene_fracciones=False,
    )
    assert nuevo["precio_umbral"] is None
    assert nuevo["precio_bajo_umbral"] is None
    assert nuevo["precio_sobre_umbral"] is None


def test_mapeo_codigo_en_blanco_se_vuelve_null():
    assert mapear_producto(_producto_viejo(codigo="   "), tiene_fracciones=False)["codigo"] is None
    assert mapear_producto(_producto_viejo(codigo=None), tiene_fracciones=False)["codigo"] is None


def test_mapeo_unidad_vacia_usa_default():
    assert mapear_producto(_producto_viejo(unidad_medida=None), tiene_fracciones=False)["unidad_medida"] == "Unidad"


# --- permite_fraccion --------------------------------------------------------------------------


def test_permite_fraccion_true_si_tiene_fracciones():
    assert mapear_producto(_producto_viejo(), tiene_fracciones=True)["permite_fraccion"] is True


def test_permite_fraccion_false_si_no_tiene():
    assert mapear_producto(_producto_viejo(), tiene_fracciones=False)["permite_fraccion"] is False


def test_construir_plan_resuelve_permite_fraccion_por_presencia():
    productos = [_producto_viejo(id=1), _producto_viejo(id=2, codigo="XYZ")]
    fracciones = {1: [{"fraccion": "1/2", "precio_total": 12500, "precio_unitario": 25000}]}
    plan = construir_plan(productos, fracciones)
    # id=1 tiene fracciones -> True; id=2 no -> False.
    assert plan[0][0]["permite_fraccion"] is True
    assert len(plan[0][1]) == 1
    assert plan[1][0]["permite_fraccion"] is False
    assert plan[1][1] == []


# --- parser de fracción ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1/2", Decimal("0.5")),
        ("1/4", Decimal("0.25")),
        ("3/4", Decimal("0.75")),
        ("2", Decimal("2")),
        ("0.5", Decimal("0.5")),
        (" 1/2 ", Decimal("0.5")),
    ],
)
def test_parsear_fraccion_validas(texto, esperado):
    assert parsear_fraccion(texto) == esperado


@pytest.mark.parametrize("texto", ["basura", "", "   ", "1/0", "a/b", None, "1/"])
def test_parsear_fraccion_invalidas_devuelven_none(texto):
    assert parsear_fraccion(texto) is None


def test_mapear_fraccion_deriva_decimal_y_castea_precios():
    nueva = mapear_fraccion({"fraccion": "1/4", "precio_total": 6250, "precio_unitario": 25000})
    assert nueva["fraccion"] == "1/4"
    assert nueva["decimal"] == Decimal("0.25")
    assert nueva["precio_total"] == Decimal("6250")
    assert nueva["precio_unitario"] == Decimal("25000")


def test_mapear_fraccion_basura_decimal_none_pero_precios_ok():
    nueva = mapear_fraccion({"fraccion": "media", "precio_total": 6250, "precio_unitario": None})
    assert nueva["decimal"] is None
    assert nueva["precio_total"] == Decimal("6250")
    assert nueva["precio_unitario"] is None


# --- dedup (idempotencia) ----------------------------------------------------------------------


def test_dedup_omite_codigo_existente():
    assert debe_omitir("ABC123", "Cemento", {"ABC123"}, set()) is True


def test_dedup_inserta_codigo_nuevo():
    assert debe_omitir("NUEVO", "Cemento", {"ABC123"}, set()) is False


def test_dedup_sin_codigo_usa_nombre_existente():
    assert debe_omitir(None, "Cemento gris 50kg", set(), {"Cemento gris 50kg"}) is True


def test_dedup_sin_codigo_nombre_nuevo_se_inserta():
    assert debe_omitir(None, "Arena lavada", set(), {"Cemento gris 50kg"}) is False


def test_dedup_con_codigo_no_mira_nombre():
    # Con codigo presente, la dedup es por codigo aunque el nombre ya exista.
    assert debe_omitir("NUEVO", "Cemento gris 50kg", {"ABC123"}, {"Cemento gris 50kg"}) is False
