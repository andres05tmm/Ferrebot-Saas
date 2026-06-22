#!/usr/bin/env python3
"""Exporta el catálogo de un tenant (Postgres) a `catalogo.json` para el replay con datos reales.

Lee `productos` + `productos_fracciones` de la app DB del tenant (la base sembrada con el dump de
Punto Rojo) y emite el formato que consume `replay.py --catalogo`:

    [{"id": 7, "nombre": "vinilo", "precio_venta": "20000.00", "iva": 0, "activo": true,
      "precio_umbral": null, "precio_bajo_umbral": null, "precio_sobre_umbral": null,
      "fracciones": [["0.5", "12000.00"]]}, ...]

Uso:
    python -m tests.evals.replay.extraer_catalogo \
        --db-url "postgresql://user:pass@host:5432/ferrebot_puntorojo" \
        --out catalogo_puntorojo.json

Notas:
  - `--db-url` debe ser una URL libpq síncrona (postgresql://...), no la `+asyncpg`.
  - Columnas según `modules/inventario/models.py` (Producto / ProductoFraccion). Si el esquema cambia,
    ajustar las dos consultas de abajo.
"""
from __future__ import annotations

import argparse
import json
import pathlib


SQL_PRODUCTOS = """
    SELECT id, nombre, precio_venta, iva, activo,
           precio_umbral, precio_bajo_umbral, precio_sobre_umbral, unidad_medida
      FROM productos
     ORDER BY id
"""

SQL_FRACCIONES = """
    SELECT producto_id, decimal, precio_total
      FROM productos_fracciones
     WHERE decimal IS NOT NULL
     ORDER BY producto_id, decimal
"""


def _s(v):
    """Decimal/num → string (preserva exactitud); None se mantiene None; bool/int tal cual."""
    if v is None or isinstance(v, bool):
        return v
    return str(v)


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Exporta el catálogo de un tenant a catalogo.json.")
    ap.add_argument("--db-url", required=True, help="URL libpq síncrona del tenant (postgresql://...)")
    ap.add_argument("--out", required=True, help="ruta de salida del catalogo.json")
    args = ap.parse_args(argv)

    import psycopg  # import perezoso: el módulo se puede importar/`--help` sin psycopg instalado

    fracciones: dict[int, list[list]] = {}
    productos: list[dict] = []
    with psycopg.connect(args.db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_FRACCIONES)
            for producto_id, decimal_, precio_total in cur.fetchall():
                fracciones.setdefault(int(producto_id), []).append([_s(decimal_), _s(precio_total)])

            cur.execute(SQL_PRODUCTOS)
            for (pid, nombre, precio_venta, iva, activo,
                 p_umbral, p_bajo, p_sobre, unidad_medida) in cur.fetchall():
                productos.append(
                    {
                        "id": int(pid),
                        "nombre": nombre,
                        "precio_venta": _s(precio_venta),
                        "iva": int(iva) if iva is not None else 0,
                        "activo": bool(activo),
                        "precio_umbral": _s(p_umbral),
                        "precio_bajo_umbral": _s(p_bajo),
                        "precio_sobre_umbral": _s(p_sobre),
                        "unidad_medida": unidad_medida or "Unidad",
                        "fracciones": fracciones.get(int(pid), []),
                    }
                )

    pathlib.Path(args.out).write_text(json.dumps(productos, ensure_ascii=False, indent=2), encoding="utf-8")
    con_frac = sum(1 for p in productos if p["fracciones"])
    con_escalonado = sum(1 for p in productos if p["precio_umbral"] is not None)
    print(f"catálogo exportado: {len(productos)} productos "
          f"({con_frac} con fracciones, {con_escalonado} con precio escalonado) → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
