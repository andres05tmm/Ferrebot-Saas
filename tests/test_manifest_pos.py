"""Pack POS del manifiesto (ADR 0011 §D3): validación (pura) + loader (BD efímera).

Tres bloques:
- VALIDACIÓN (sin BD): cada regla nueva del pack POS rompe con un `ErrorManifiesto` claro, y el
  ejemplo de ferretería valida OK.
- LOADER idempotente (Postgres real, fixture `tenant`): sembrar dos veces deja las MISMAS filas y
  NO doble-cuenta el stock (el movimiento ENTRADA tiene idempotency_key).
- PARIDAD: las filas sembradas por el manifiesto coinciden 1:1 con lo esperado a mano.
"""
from __future__ import annotations

import copy
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
import yaml
from psycopg.rows import dict_row

from core.db.urls import to_libpq
from tools.manifest import ErrorManifiesto, Manifiesto, cargar_manifiesto, validar
from tools.manifest.packs.pos import cargar_pos
from tools.manifest.packs.registry import PACKS

_EJEMPLO = Path(__file__).parents[1] / "tools" / "onboarding" / "ferreteria-demo.manifest.example.yaml"


def _datos_ejemplo() -> dict:
    return yaml.safe_load(_EJEMPLO.read_text(encoding="utf-8"))


def _manifiesto(datos: dict) -> Manifiesto:
    return Manifiesto.model_validate(datos)


def _conteo(conn, tabla: str) -> int:
    return conn.execute(f"SELECT count(*) AS n FROM {tabla}").fetchone()["n"]


# =====================================================================================
# VALIDACIÓN (pura, sin BD)
# =====================================================================================

def test_ejemplo_pos_parsea_y_valida_ok():
    manifiesto = cargar_manifiesto(_EJEMPLO)
    validar(manifiesto)  # no lanza

    pos = manifiesto.packs.pos
    assert pos is not None
    assert len(pos.productos) == 3
    assert len(pos.aliases) == 3
    pintura = next(p for p in pos.productos if p.codigo == "PIN-GAL")
    assert pintura.permite_fraccion is True
    assert len(pintura.fracciones) == 2
    tornillo = next(p for p in pos.productos if p.codigo == "TOR-14")
    assert tornillo.escalonado is not None and tornillo.escalonado.sobre == 150


def test_pos_sin_flag_pos_falla():
    # Datos de pos declarados pero la feature `pos` no está activa → incoherencia (falla cerrado).
    datos = _datos_ejemplo()
    datos["plan"]["features"] = []  # quita pos
    with pytest.raises(ErrorManifiesto, match="pos no está activa"):
        validar(_manifiesto(datos))


def test_precio_venta_no_positivo_falla():
    datos = _datos_ejemplo()
    datos["packs"]["pos"]["productos"][0]["precio_venta"] = 0
    with pytest.raises(ErrorManifiesto, match="precio_venta debe ser > 0"):
        validar(_manifiesto(datos))


def test_iva_invalido_falla():
    datos = _datos_ejemplo()
    datos["packs"]["pos"]["productos"][0]["iva"] = 16
    with pytest.raises(ErrorManifiesto, match="iva inválido 16"):
        validar(_manifiesto(datos))


def test_fracciones_sin_permite_fraccion_falla():
    datos = _datos_ejemplo()
    # El simple (cemento) no permite fracción; agregarle una es incoherente.
    datos["packs"]["pos"]["productos"][0]["fracciones"] = [
        {"fraccion": "1/2", "precio_total": 14000}
    ]
    with pytest.raises(ErrorManifiesto, match="permite_fraccion es false"):
        validar(_manifiesto(datos))


def test_fraccion_incoherente_falla():
    # decimal × precio_unitario debe ≈ precio_total (tolerancia 1 peso).
    datos = _datos_ejemplo()
    datos["packs"]["pos"]["productos"][1]["fracciones"][1] = {
        "fraccion": "1/2", "decimal": 0.5, "precio_total": 34000, "precio_unitario": 99999
    }
    with pytest.raises(ErrorManifiesto, match="incoherente"):
        validar(_manifiesto(datos))


def test_alias_a_producto_inexistente_falla():
    datos = _datos_ejemplo()
    datos["packs"]["pos"]["aliases"][0]["producto"] = "Producto Fantasma"
    with pytest.raises(ErrorManifiesto, match="Producto Fantasma.*no está declarado"):
        validar(_manifiesto(datos))


def test_nombre_duplicado_normalizado_falla():
    datos = _datos_ejemplo()
    # Mismo nombre que el primero salvo mayúsculas/espacios → colisión de clave natural.
    clon = copy.deepcopy(datos["packs"]["pos"]["productos"][0])
    clon["codigo"] = "OTRO"
    clon["nombre"] = "  CEMENTO   gris 50KG "
    datos["packs"]["pos"]["productos"].append(clon)
    with pytest.raises(ErrorManifiesto, match="nombre de producto duplicado"):
        validar(_manifiesto(datos))


def test_escalonado_no_positivo_falla():
    datos = _datos_ejemplo()
    datos["packs"]["pos"]["productos"][2]["escalonado"]["sobre"] = 0
    with pytest.raises(ErrorManifiesto, match="escalonado.sobre debe ser > 0"):
        validar(_manifiesto(datos))


def test_reune_varios_errores_pos():
    datos = _datos_ejemplo()
    datos["packs"]["pos"]["productos"][0]["precio_venta"] = -1
    datos["packs"]["pos"]["productos"][2]["iva"] = 7
    with pytest.raises(ErrorManifiesto) as exc:
        validar(_manifiesto(datos))
    msg = str(exc.value)
    assert "precio_venta" in msg and "iva inválido 7" in msg
    assert "2 error(es)" in msg


# =====================================================================================
# LOADER (Postgres real)
# =====================================================================================

async def test_cargar_pos_siembra_y_es_idempotente(tenant):
    pos = cargar_manifiesto(_EJEMPLO).packs.pos
    assert pos is not None

    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_pos(pos, conn)
        conn.commit()

        # Conteos base.
        assert _conteo(conn, "productos") == 3
        assert _conteo(conn, "productos_fracciones") == 2
        assert _conteo(conn, "aliases") == 3
        assert _conteo(conn, "inventario") == 3
        assert _conteo(conn, "movimientos_inventario") == 3

        # Stock de apertura aplicado vía movimiento ENTRADA (regla 7).
        stock_cemento = conn.execute(
            "SELECT i.stock_actual FROM inventario i JOIN productos p ON p.id = i.producto_id "
            "WHERE p.codigo = %s", ("CEM-50",)
        ).fetchone()["stock_actual"]
        assert stock_cemento == Decimal("100")
        n_entradas = conn.execute(
            "SELECT count(*) AS n FROM movimientos_inventario WHERE tipo = 'ENTRADA'"
        ).fetchone()["n"]
        assert n_entradas == 3

        # Idempotencia DURA: re-correr no cambia conteos NI duplica el stock.
        antes = {t: _conteo(conn, t) for t in
                 ("productos", "productos_fracciones", "aliases", "inventario", "movimientos_inventario")}
        cargar_pos(pos, conn)
        conn.commit()
        despues = {t: _conteo(conn, t) for t in antes}
        assert antes == despues
        stock_cemento_2 = conn.execute(
            "SELECT i.stock_actual FROM inventario i JOIN productos p ON p.id = i.producto_id "
            "WHERE p.codigo = %s", ("CEM-50",)
        ).fetchone()["stock_actual"]
        assert stock_cemento_2 == Decimal("100")  # NO se duplicó a 200


async def test_pos_paridad_filas_esperadas(tenant):
    """PARIDAD (ADR 0011 §D6): las filas sembradas por el manifiesto coinciden con lo esperado a mano."""
    pos = cargar_manifiesto(_EJEMPLO).packs.pos
    with psycopg.connect(to_libpq(tenant.url), row_factory=dict_row) as conn:
        cargar_pos(pos, conn)
        conn.commit()

        productos = {
            r["codigo"]: r for r in conn.execute(
                "SELECT codigo, nombre, categoria, unidad_medida, precio_venta, precio_compra, iva, "
                "permite_fraccion, precio_umbral, precio_bajo_umbral, precio_sobre_umbral, activo "
                "FROM productos"
            ).fetchall()
        }
        # Simple.
        cem = productos["CEM-50"]
        assert cem["nombre"] == "Cemento Gris 50kg" and cem["categoria"] == "Construcción"
        assert cem["unidad_medida"] == "bulto" and cem["precio_venta"] == Decimal("28000")
        assert cem["precio_compra"] == Decimal("24000") and cem["iva"] == 19
        assert cem["permite_fraccion"] is False and cem["activo"] is True
        assert cem["precio_umbral"] is None  # sin escalonado

        # Con fracciones.
        pin = productos["PIN-GAL"]
        assert pin["permite_fraccion"] is True and pin["precio_venta"] == Decimal("65000")
        fracciones = {
            r["fraccion"]: (r["decimal"], r["precio_total"], r["precio_unitario"])
            for r in conn.execute(
                "SELECT f.fraccion, f.decimal, f.precio_total, f.precio_unitario "
                "FROM productos_fracciones f JOIN productos p ON p.id = f.producto_id "
                "WHERE p.codigo = %s", ("PIN-GAL",)
            ).fetchall()
        }
        assert fracciones["1/4"] == (Decimal("0.250"), Decimal("18000"), None)
        assert fracciones["1/2"] == (Decimal("0.500"), Decimal("34000"), Decimal("68000"))

        # Escalonado.
        tor = productos["TOR-14"]
        assert tor["precio_umbral"] == Decimal("100.000")
        assert tor["precio_bajo_umbral"] == Decimal("200")
        assert tor["precio_sobre_umbral"] == Decimal("150")
        assert tor["precio_compra"] is None

        # Aliases resueltos a su producto por nombre.
        alias = conn.execute(
            "SELECT a.termino, a.reemplazo, p.codigo AS prod FROM aliases a "
            "LEFT JOIN productos p ON p.id = a.producto_id WHERE a.termino = %s",
            ("cemento",),
        ).fetchone()
        assert alias["reemplazo"] == "Cemento Gris 50kg" and alias["prod"] == "CEM-50"


def test_pos_registrado_con_loader():
    # El registro ya NO tiene loader=None para pos: el provisionador correrá cargar_pos.
    assert PACKS["pos"].loader is cargar_pos
