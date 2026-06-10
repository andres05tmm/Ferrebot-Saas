"""Eval dorado (ADR 0011 §D7, F5): comparador PURO + carga + umbrales. Sin BD ni red.

Cubre: match fuzzy por nombre, exactitud por campo solo cuando ambos lados tienen el dato, cobertura,
misses concretos (faltantes + discordancias), umbrales PASS/FAIL, costo/duración N/A sin sidecar, y la
carga desde YAML (manifiesto) y CSV.
"""
from __future__ import annotations

from pathlib import Path

from tools.eval_extractor import (
    UMBRAL_NOMBRE_RECALL,
    aplicar_umbrales,
    cargar_productos,
    comparar,
    evaluar,
    reporte_json,
    reporte_markdown,
)

_EJEMPLO = Path(__file__).parents[1] / "tools" / "onboarding" / "ferreteria-demo.manifest.example.yaml"


def _prod(nombre, precio, unidad="unidad", frac=False, fracciones=None, escalonado=None) -> dict:
    return {
        "nombre": nombre, "precio_venta": precio, "unidad_medida": unidad,
        "permite_fraccion": frac, "fracciones": fracciones, "escalonado": escalonado,
    }


def test_match_perfecto_todo_correcto():
    gt = [_prod("Cemento Gris 50kg", 28000, "bulto"), _prod("Tornillo 1/4", 200)]
    cand = [_prod("Cemento Gris 50kg", 28000, "bulto"), _prod("Tornillo 1/4", 200)]
    r = comparar(cand, gt)
    assert r.n_emparejados == 2
    assert r.nombre_recall == 1.0
    assert r.cobertura == 1.0
    assert r.campos["precio_venta"].exactitud == 1.0
    assert r.faltantes == [] and r.discordancias == []


def test_match_fuzzy_tolera_espacios_y_mayusculas():
    # Espacios de borde/internos y mayúsculas: la normalización los colapsa → match perfecto.
    gt = [_prod("  CEMENTO   Gris   50kg ", 28000, "bulto")]
    cand = [_prod("cemento gris 50kg", 28000, "bulto")]
    r = comparar(cand, gt, umbral_nombre=0.9)
    assert r.n_emparejados == 1


def test_match_fuzzy_tolera_typo_leve():
    # Un typo de una letra ("Griz") sigue por encima del umbral por defecto (0.9).
    gt = [_prod("Cemento Griz 50kg", 28000, "bulto")]
    cand = [_prod("Cemento Gris 50kg", 28000, "bulto")]
    r = comparar(cand, gt, umbral_nombre=0.9)
    assert r.n_emparejados == 1


def test_precio_mal_es_discordancia_no_match_de_precio():
    gt = [_prod("Cemento Gris 50kg", 28000, "bulto")]
    cand = [_prod("Cemento Gris 50kg", 400000, "bulto")]  # la lija de $400k
    r = comparar(cand, gt)
    assert r.n_emparejados == 1
    assert r.campos["precio_venta"].exactitud == 0.0
    assert any("precio_venta" in d for d in r.discordancias)


def test_faltante_se_reporta():
    gt = [_prod("Producto Raro XYZ", 5000)]
    cand = [_prod("Otra Cosa Totalmente Distinta", 5000)]
    r = comparar(cand, gt, umbral_nombre=0.9)
    assert r.n_emparejados == 0
    assert len(r.faltantes) == 1 and "Producto Raro XYZ" in r.faltantes[0]


def test_campo_no_comparable_no_penaliza():
    # Ground truth desde CSV: fracciones/escalonado None → no entran al denominador.
    gt = [_prod("Cemento Gris 50kg", 28000, "bulto", fracciones=None, escalonado=None)]
    cand = [_prod("Cemento Gris 50kg", 28000, "bulto", fracciones=[("1/2", 14000)])]
    r = comparar(cand, gt)
    assert r.campos["fracciones"].comparados == 0
    assert r.campos["fracciones"].exactitud is None


def test_umbrales_pass_y_fail():
    # 100% nombre + 100% precio → PASS global (costo/duración N/A no reprueban).
    gt = [_prod("A", 10), _prod("B", 20)]
    r = comparar(gt, gt)
    r.veredictos = aplicar_umbrales(r)
    assert r.veredictos["nombre_recall"] == "PASS"
    assert r.veredictos["precio_exactitud"] == "PASS"
    assert r.veredictos["costo"] == "N/A" and r.veredictos["duracion"] == "N/A"
    assert r.veredictos["GLOBAL"] == "PASS"

    # Recall bajo el umbral → FAIL global.
    flojo = comparar([_prod("Zzz", 99)], [_prod("A", 10), _prod("B", 20)])
    flojo.veredictos = aplicar_umbrales(flojo)
    assert flojo.nombre_recall < UMBRAL_NOMBRE_RECALL
    assert flojo.veredictos["GLOBAL"] == "FAIL"


def test_costo_sidecar_reprueba_si_excede():
    gt = [_prod("A", 10)]
    r = comparar(gt, gt)
    r.costo_usd, r.duracion_min = 9.0, 12.0  # ambos exceden
    r.veredictos = aplicar_umbrales(r)
    assert r.veredictos["costo"] == "FAIL"
    assert r.veredictos["duracion"] == "FAIL"
    assert r.veredictos["GLOBAL"] == "FAIL"


def test_carga_yaml_del_ejemplo():
    productos = cargar_productos(str(_EJEMPLO))
    assert len(productos) == 3
    nombres = {p["nombre"] for p in productos}
    assert "Cemento Gris 50kg" in nombres


def test_carga_csv(tmp_path):
    csv_path = tmp_path / "gt.csv"
    csv_path.write_text(
        "nombre,precio_venta,unidad_medida,permite_fraccion\n"
        "Cemento Gris 50kg,28000,bulto,false\n"
        "Pintura Vinilo Galón,65000,galón,true\n",
        encoding="utf-8",
    )
    productos = cargar_productos(str(csv_path))
    assert len(productos) == 2
    assert productos[0]["precio_venta"] == 28000
    assert productos[1]["permite_fraccion"] is True
    assert productos[0]["fracciones"] is None  # no comparable desde CSV


def test_evaluar_extremo_a_extremo_yaml_contra_csv(tmp_path):
    # Candidato = ejemplo YAML; ground truth = CSV con los mismos 3 nombres y precios → PASS.
    csv_path = tmp_path / "gt.csv"
    csv_path.write_text(
        "nombre,precio_venta,unidad_medida\n"
        "Cemento Gris 50kg,28000,bulto\n"
        "Pintura Vinilo Galón,65000,galón\n"
        "Tornillo 1/4,200,unidad\n",
        encoding="utf-8",
    )
    r = evaluar(str(_EJEMPLO), str(csv_path))
    assert r.cobertura == 1.0
    assert r.veredictos["GLOBAL"] == "PASS"
    # Los reportes se generan sin reventar y reflejan el veredicto.
    assert "Veredicto global" in reporte_markdown(r)
    assert reporte_json(r)["veredictos"]["GLOBAL"] == "PASS"
