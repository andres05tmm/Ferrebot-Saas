"""Métrica del runner de replay: clasificación de NO-aciertos en dos niveles (decisión del owner).

PELIGROSO = sobre-registro grosero (>=10x) o registro indebido (registró cuando debía deferir).
EQUIVOCACION = registró con total/items fuera de tolerancia pero sin ser grosero (deriva/variante).
El acierto sigue estricto; estos niveles solo segmentan los fallos. Pura stdlib (sin BD ni LLM).
"""
from decimal import Decimal
from types import SimpleNamespace

from tests.evals.replay.replay import _evaluar, _nivel_sobre_registro, agregar


def _header(total, lineas):
    return SimpleNamespace(
        total=Decimal(total),
        lineas=[SimpleNamespace(producto_id=pid, cantidad=Decimal(c)) for pid, c in lineas],
    )


def test_nivel_sobre_registro_grosero_vs_suave():
    assert _nivel_sobre_registro(Decimal("3750000"), Decimal("7500")) == "fail_peligroso"   # x500
    assert _nivel_sobre_registro(Decimal("65000"), Decimal("10000")) == "fail_equivocacion"  # x6.5
    assert _nivel_sobre_registro(Decimal("3500"), Decimal("4000")) == "fail_equivocacion"     # deriva


def test_evaluar_acierto_exacto():
    caso = {"espera": "venta", "items": [[9, "500"]], "total": "7500.00"}
    assert _evaluar(caso, "venta", _header("7500.00", [(9, "500")]))[0] == "ok"


def test_evaluar_sobre_registro_grosero_es_peligroso():
    # El bug clásico: "500 puntilla" registrado como 500 × precio_caja = millones.
    caso = {"espera": "venta", "items": [[9, "500"]], "total": "7500.00"}
    assert _evaluar(caso, "venta", _header("3750000.00", [(9, "500")]))[0] == "fail_peligroso"


def test_evaluar_deriva_de_precio_es_equivocacion_no_peligroso():
    caso = {"espera": "venta", "items": [[79, "1"]], "total": "4000"}
    assert _evaluar(caso, "venta", _header("3500.00", [(79, "1")]))[0] == "fail_equivocacion"


def test_evaluar_registro_indebido_es_peligroso():
    # Esperaba deferir/preguntar y registró igual: lo más peligroso (sin pedir confirmación).
    caso = {"espera": "defiere", "categoria": "escalonado"}
    assert _evaluar(caso, "venta", _header("60000.00", [(11, "3")]))[0] == "fail_registro_indebido"


def test_evaluar_items_distintos_es_equivocacion():
    caso = {"espera": "venta", "items": [[9, "500"]], "total": "7500.00"}
    assert _evaluar(caso, "venta", _header("7500.00", [(99, "500")]))[0] == "fail_equivocacion"


def test_agregar_cuenta_los_dos_niveles_por_separado():
    filas = [
        {"categoria": "a", "got": "venta", "outcome": "ok"},
        {"categoria": "a", "got": "venta", "outcome": "fail_peligroso"},
        {"categoria": "a", "got": "venta", "outcome": "fail_equivocacion"},
        {"categoria": "b", "got": "defiere", "outcome": "fail_no_registro"},
    ]
    cats, glob = agregar(filas)
    assert glob == {"n": 4, "ok": 1, "peligrosos": 1, "equivocaciones": 1, "resolvio": 3, "defirio": 1}
    assert cats["a"] == {"n": 3, "ok": 1, "peligrosos": 1, "equivocaciones": 1}
