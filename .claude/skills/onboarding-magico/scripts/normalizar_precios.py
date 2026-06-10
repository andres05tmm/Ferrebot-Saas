#!/usr/bin/env python3
"""Normalización determinista de filas crudas de catálogo (skill onboarding-magico, ADR 0011 §D1).

Entrada: CSV o JSON con filas crudas del MAP. Columnas/claves esperadas (faltantes = null):
    nombre_visto, precio_visto, unidad_vista, categoria, origen
Salida (stdout): JSON con {filas: [...], dudas: [...], stats: {...}}.

Hace lo que NO debe hacer el modelo a criterio:
- Parseo de precios colombianos: "12.500", "$ 12,500", "12,5k", "12.500/m", "1.234.567".
- Normalización de nombre (lower/trim/colapso de espacios) — espejo de tools/manifest/schema.py.
- Duplicados por nombre normalizado.
- Outliers de precio por categoría (> FACTOR x la mediana, default 5) → DUDA obligatoria.

Solo stdlib. Uso:
    python normalizar_precios.py filas.csv [--factor-outlier 5] > normalizado.json
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from pathlib import Path

_ESPACIOS = re.compile(r"\s+")
# "12.500", "$ 12,500.00", "12,5k", "12.500/m" → captura el número y un sufijo k opcional.
_PRECIO = re.compile(r"\$?\s*([\d.,]+)\s*([kK])?")


def normalizar_nombre(nombre: str) -> str:
    """Espejo de tools/manifest/schema.py::normalizar_nombre (misma clave natural del upsert)."""
    return _ESPACIOS.sub(" ", nombre).strip().lower()


def parsear_precio_co(texto: str | int | float | None) -> int | None:
    """Precio colombiano → entero en pesos. None/ilegible → None (NUNCA adivinar).

    Reglas: separador de miles '.' o ','; decimales solo si el último grupo tiene 1-2 dígitos tras
    el ÚLTIMO separador y hay un solo separador de ese tipo (caso "12,5" o "12.50"); sufijo k = x1000.
    """
    if texto is None:
        return None
    if isinstance(texto, (int, float)):
        return int(round(float(texto)))
    m = _PRECIO.search(str(texto).strip())
    if not m:
        return None
    num, k = m.group(1), bool(m.group(2))
    num = num.strip(".,")
    if not num:
        return None
    # ¿El último separador delimita decimales (1-2 dígitos) o miles (3 dígitos)?
    ultimo = max(num.rfind("."), num.rfind(","))
    if ultimo != -1 and len(num) - ultimo - 1 in (1, 2):
        entero = re.sub(r"[.,]", "", num[:ultimo])
        dec = num[ultimo + 1:]
        valor = float(f"{entero or 0}.{dec}")
    else:
        valor = float(re.sub(r"[.,]", "", num))
    if k:
        valor *= 1000
    return int(round(valor))


def detectar_outliers(filas: list[dict], factor: float) -> list[dict]:
    """Precio > factor x mediana de su categoría (o global si no hay categoría) → duda."""
    dudas: list[dict] = []
    por_cat: dict[str, list[int]] = {}
    for f in filas:
        if f["precio"] is not None:
            por_cat.setdefault(f.get("categoria") or "_global", []).append(f["precio"])
    medianas = {c: statistics.median(v) for c, v in por_cat.items() if v}
    for f in filas:
        if f["precio"] is None:
            continue
        mediana = medianas.get(f.get("categoria") or "_global")
        if mediana and f["precio"] > factor * mediana:
            dudas.append({
                "tipo": "outlier_precio", "nombre": f["nombre"], "precio": f["precio"],
                "mediana_categoria": mediana, "origen": f.get("origen"),
                "mensaje": f"precio {f['precio']} > {factor}x la mediana ({mediana:g}) de su categoría",
            })
    return dudas


def procesar(filas_crudas: list[dict], factor: float) -> dict:
    filas, dudas = [], []
    vistos: dict[str, dict] = {}
    for cruda in filas_crudas:
        nombre = (cruda.get("nombre_visto") or "").strip()
        if not nombre:
            dudas.append({"tipo": "sin_nombre", "origen": cruda.get("origen"),
                          "mensaje": "fila sin nombre legible"})
            continue
        precio = parsear_precio_co(cruda.get("precio_visto"))
        if precio is None:
            dudas.append({"tipo": "precio_ilegible", "nombre": nombre, "origen": cruda.get("origen"),
                          "mensaje": f"precio no parseable: {cruda.get('precio_visto')!r}"})
        fila = {
            "nombre": nombre, "nombre_norm": normalizar_nombre(nombre), "precio": precio,
            "unidad": (cruda.get("unidad_vista") or None),
            "categoria": (cruda.get("categoria") or None), "origen": cruda.get("origen"),
        }
        previa = vistos.get(fila["nombre_norm"])
        if previa is not None:
            if previa["precio"] != fila["precio"]:
                dudas.append({"tipo": "duplicado_conflicto", "nombre": nombre,
                              "precios": [previa["precio"], fila["precio"]],
                              "origen": [previa.get("origen"), fila.get("origen")],
                              "mensaje": "mismo producto con precios distintos en dos insumos"})
            continue  # dedupe: gana la primera aparición; el conflicto queda como duda
        vistos[fila["nombre_norm"]] = fila
        filas.append(fila)

    dudas.extend(detectar_outliers(filas, factor))
    return {
        "filas": filas, "dudas": dudas,
        "stats": {"filas_entrada": len(filas_crudas), "filas_unicas": len(filas),
                  "dudas": len(dudas),
                  "sin_precio": sum(1 for f in filas if f["precio"] is None)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("archivo", help="CSV o JSON con las filas crudas del MAP")
    ap.add_argument("--factor-outlier", type=float, default=5.0)
    args = ap.parse_args()

    ruta = Path(args.archivo)
    if ruta.suffix.lower() == ".json":
        filas_crudas = json.loads(ruta.read_text(encoding="utf-8"))
    else:
        with ruta.open(encoding="utf-8-sig", newline="") as fh:
            filas_crudas = list(csv.DictReader(fh))

    print(json.dumps(procesar(filas_crudas, args.factor_outlier), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
