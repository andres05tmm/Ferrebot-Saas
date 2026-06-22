#!/usr/bin/env python3
"""Extrae un corpus de replay desde la DB de PRODUCCIÓN del bot viejo (bot-ventas-ferreteria).

Dos fuentes (elige con --modo):

  ventas (recomendado para medir acierto de ventas, etiquetado automático):
      Reconstruye frases de venta desde `ventas_detalle`: cada línea con `alias_usado` + `cantidad`
      se vuelve una frase aproximada ("2 martillo") con su resultado esperado (producto_id, cantidad).
      Es de alta fidelidad para el camino bypass (venta de un solo producto).

  conversaciones (texto REAL del vendedor, sin etiqueta):
      Vuelca `conversaciones_bot.content` con role='user'. Son los mensajes tal cual se escribieron;
      sirven para la ruta LLM y para etiquetar a mano los casos complejos (gastos, fiados, consultas).

Uso:
    python -m tests.evals.replay.extraer_corpus --db-url "postgresql://...prod..." \
        --modo ventas --out corpus_puntorojo.jsonl

    python -m tests.evals.replay.extraer_corpus --db-url "postgresql://...prod..." \
        --modo conversaciones --out corpus_mensajes.jsonl

IMPORTANTE:
  - Esta DB es la del bot viejo (Railway), NO está en el repo. Exporta/usa su `DATABASE_URL`.
  - VERIFICAR los nombres de columna contra el esquema del bot viejo
    (migrations/003_migrate_ventas.py para ventas_detalle, 018_conversaciones_bot.py para conversaciones).
  - El `producto_id` reconstruido sólo coincide con el catálogo del tenant nuevo si la migración
    preservó los ids (el restore del dump los preserva). Si no, re-mapear por nombre.
  - El texto reconstruido en modo 'ventas' es una APROXIMACIÓN (el alias, no el mensaje literal):
    fiel para ventas simples; para el mensaje exacto usa modo 'conversaciones'.
"""
from __future__ import annotations

import argparse
import json
import pathlib


# Una línea de detalle = un caso de venta de un solo producto (lo que resuelve el bypass).
# Ajustar columnas si el esquema del bot viejo difiere.
SQL_VENTAS = """
    SELECT vd.alias_usado, vd.cantidad, vd.producto_id
      FROM ventas_detalle vd
     WHERE vd.alias_usado IS NOT NULL
       AND vd.cantidad IS NOT NULL
       AND vd.producto_id IS NOT NULL
     ORDER BY vd.id
"""

SQL_CONVERSACIONES = """
    SELECT chat_id, content
      FROM conversaciones_bot
     WHERE role = 'user'
       AND content IS NOT NULL
     ORDER BY creado
"""


def _cantidad_legible(cantidad) -> str:
    """Decimal de cantidad → texto natural para la frase ('2', '0.5'→'medio')."""
    from decimal import Decimal

    c = Decimal(str(cantidad))
    if c == c.to_integral_value():
        return str(int(c))
    if c == Decimal("0.5"):
        return "medio"
    if c == Decimal("0.25"):
        return "un cuarto"
    return str(c.normalize())


def _extraer_ventas(cur) -> list[dict]:
    cur.execute(SQL_VENTAS)
    casos = []
    for alias_usado, cantidad, producto_id in cur.fetchall():
        from decimal import Decimal

        cant = Decimal(str(cantidad))
        casos.append(
            {
                "frase": f"{_cantidad_legible(cantidad)} {alias_usado}".strip(),
                "espera": "venta",
                "items": [[int(producto_id), str(cant.normalize())]],
                "categoria": "reconstruida_ventas",
                "fuente": "ventas_detalle",
            }
        )
    return casos


def _extraer_conversaciones(cur) -> list[dict]:
    cur.execute(SQL_CONVERSACIONES)
    casos = []
    for chat_id, content in cur.fetchall():
        texto = (content or "").strip()
        if texto:
            casos.append(
                {
                    "frase": texto,
                    "espera": "?",            # sin etiquetar: etiquétalo a mano o úsalo en la ruta LLM
                    "categoria": "conversacion",
                    "fuente": "conversaciones_bot",
                }
            )
    return casos


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Extrae corpus de replay de la DB del bot viejo.")
    ap.add_argument("--db-url", required=True, help="URL libpq síncrona de PRODUCCIÓN (postgresql://...)")
    ap.add_argument("--modo", choices=["ventas", "conversaciones"], default="ventas")
    ap.add_argument("--out", required=True, help="ruta de salida del corpus JSONL")
    ap.add_argument("--limite", type=int, default=0, help="máximo de casos (0 = sin límite)")
    args = ap.parse_args(argv)

    import psycopg  # import perezoso

    with psycopg.connect(args.db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            casos = _extraer_ventas(cur) if args.modo == "ventas" else _extraer_conversaciones(cur)

    if args.limite > 0:
        casos = casos[: args.limite]

    with pathlib.Path(args.out).open("w", encoding="utf-8") as fh:
        for caso in casos:
            fh.write(json.dumps(caso, ensure_ascii=False) + "\n")

    print(f"corpus '{args.modo}' extraído: {len(casos)} casos → {args.out}")
    if args.modo == "conversaciones":
        print("  (espera='?': etiqueta los casos o úsalos en la ruta LLM; el runner bypass los omitirá)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
