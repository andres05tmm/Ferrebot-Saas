"""Eval dorado del onboarding mágico (ADR 0011 §D7): mide la calidad de una extracción contra el
catálogo REAL como ground truth, con umbrales PASS/FAIL.

Qué compara
-----------
Un **manifiesto candidato** (el pack `pos` que produjo la extracción) contra un **ground truth** (el
catálogo real, export CSV o un YAML esperado). El ground truth de Punto Rojo (600+ productos) es la
definición de "robusto": un precio mal leído es peor que un faltante (la lija de $400k).

v1 vs v2 (decisión consciente — ver el reporte / ADR §D4, §D7)
-------------------------------------------------------------
En v1 la extracción la hace Cowork EN SESIÓN (costo API $0): no hay pipeline de código que "correr",
así que el eval AUDITA el artefacto producido (el YAML candidato) contra el ground truth. Cuando
aterrice F2 (pipeline API), el mismo eval correrá el pipeline para generar el candidato y además
medirá costo/duración por corrida (knob `--map-model`). Por eso costo/duración se leen de un sidecar
de métricas (`--metricas`) si existe, y si no se reportan como N/A sin reprobar (en v1 no hay API).

Métricas (sobre el manifiesto FINAL, no sobre el map crudo)
-----------------------------------------------------------
- nombre: recall por match fuzzy (≥ `--umbral-nombre`, default 0.9) — % del ground truth reencontrado.
- precio_venta: exactitud EXACTA sobre los productos emparejados.
- unidad_medida / permite_fraccion / fracciones / escalonado: exactitud sobre emparejados.
- cobertura: emparejados / total ground truth.
- El reporte enseña los MISSES concretos (faltantes + campos discordantes) para iterar los prompts.

Umbrales (ADR §D7): nombre ≥ 98%, precio ≥ 99%, costo ≤ USD 8, duración ≤ 10 min.

NO entra al CI (necesita los insumos dorados, gitignored junto a tools/onboarding/). Cómo correrlo:
docs/runbook.md §"Eval del onboarding".

    python -m tools.eval_extractor --manifiesto tools/onboarding/puntorojo.yaml \
        --ground-truth tools/onboarding/puntorojo-catalogo.csv --salida eval-puntorojo
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from tools.manifest import cargar_manifiesto
from tools.manifest.schema import normalizar_nombre

# Umbrales de aceptación (ADR 0011 §D7). costo/duración solo aplican cuando hay sidecar de métricas.
UMBRAL_NOMBRE_RECALL = 0.98
UMBRAL_PRECIO_EXACTITUD = 0.99
UMBRAL_COSTO_USD = 8.0
UMBRAL_DURACION_MIN = 10.0


# ---------------------------------------------------------------------------
# Carga de productos (candidato y ground truth)
# ---------------------------------------------------------------------------
def _producto_a_dict(p) -> dict:
    """ProductoPos (schema) → dict plano comparable. Fracciones/escalonado como estructuras canónicas."""
    return {
        "nombre": p.nombre,
        "precio_venta": p.precio_venta,
        "unidad_medida": p.unidad_medida,
        "permite_fraccion": p.permite_fraccion,
        "fracciones": sorted(
            (f.fraccion, f.precio_total) for f in p.fracciones
        ),
        "escalonado": None if p.escalonado is None else (
            p.escalonado.umbral, p.escalonado.bajo, p.escalonado.sobre
        ),
    }


def cargar_productos_yaml(path: str | Path) -> list[dict]:
    """Productos del pack `pos` de un manifiesto (YAML/JSON). Sirve para candidato y para ground truth."""
    manifiesto = cargar_manifiesto(path)
    pos = manifiesto.packs.pos
    return [_producto_a_dict(p) for p in pos.productos] if pos is not None else []


def cargar_productos_csv(path: str | Path) -> list[dict]:
    """Ground truth desde CSV. Columnas: nombre, precio_venta (requeridas); unidad_medida,
    permite_fraccion (opcionales). Fracciones/escalonado no se comparan desde CSV (usar YAML)."""
    productos: list[dict] = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for fila in csv.DictReader(fh):
            productos.append({
                "nombre": (fila.get("nombre") or "").strip(),
                "precio_venta": int(float(fila["precio_venta"])) if fila.get("precio_venta") else None,
                "unidad_medida": (fila.get("unidad_medida") or "").strip() or None,
                "permite_fraccion": _a_bool(fila.get("permite_fraccion")),
                "fracciones": None,    # no comparable desde CSV
                "escalonado": None,    # no comparable desde CSV
            })
    return productos


def _a_bool(valor: str | None) -> bool | None:
    if valor is None or valor.strip() == "":
        return None
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes", "x"}


def cargar_productos(path: str | Path) -> list[dict]:
    """Despacha por extensión: .csv → CSV; el resto (.yaml/.yml/.json) → manifiesto."""
    return cargar_productos_csv(path) if str(path).lower().endswith(".csv") else cargar_productos_yaml(path)


# ---------------------------------------------------------------------------
# Comparación (PURA, sin IO)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class CampoMetrica:
    comparados: int = 0   # emparejados donde AMBOS lados tienen el campo (denominador)
    correctos: int = 0

    @property
    def exactitud(self) -> float | None:
        return None if self.comparados == 0 else self.correctos / self.comparados


@dataclass(slots=True)
class ResultadoEval:
    n_candidato: int
    n_esperado: int
    n_emparejados: int
    nombre_recall: float
    campos: dict[str, CampoMetrica]
    faltantes: list[str]          # ground truth sin pareja en el candidato (misses concretos)
    discordancias: list[str]      # emparejados con algún campo distinto (misses concretos)
    costo_usd: float | None = None
    duracion_min: float | None = None
    veredictos: dict[str, str] = field(default_factory=dict)

    @property
    def cobertura(self) -> float | None:
        return None if self.n_esperado == 0 else self.n_emparejados / self.n_esperado


def _mejor_match(nombre: str, candidatos: list[dict], usados: set[int]) -> tuple[int | None, float]:
    """Índice del candidato con mayor similitud de nombre (no usado) y su score 0..1."""
    objetivo = normalizar_nombre(nombre)
    mejor_i, mejor_score = None, 0.0
    for i, c in enumerate(candidatos):
        if i in usados:
            continue
        score = fuzz.token_sort_ratio(objetivo, normalizar_nombre(c["nombre"])) / 100.0
        if score > mejor_score:
            mejor_i, mejor_score = i, score
    return mejor_i, mejor_score


_CAMPOS = ("precio_venta", "unidad_medida", "permite_fraccion", "fracciones", "escalonado")


def comparar(candidato: list[dict], esperado: list[dict], *, umbral_nombre: float = 0.9) -> ResultadoEval:
    """Empareja por nombre fuzzy (≥ umbral) y mide exactitud por campo sobre los emparejados. PURO.

    Un campo solo cuenta cuando AMBOS lados lo tienen (p. ej. fracciones desde CSV = None → no penaliza).
    """
    campos = {c: CampoMetrica() for c in _CAMPOS}
    usados: set[int] = set()
    emparejados = 0
    faltantes: list[str] = []
    discordancias: list[str] = []

    for e in esperado:
        i, score = _mejor_match(e["nombre"], candidato, usados)
        if i is None or score < umbral_nombre:
            faltantes.append(f"{e['nombre']} (mejor score {score:.2f})")
            continue
        usados.add(i)
        emparejados += 1
        c = candidato[i]
        diffs: list[str] = []
        for campo in _CAMPOS:
            ev, cv = e.get(campo), c.get(campo)
            if ev is None or cv is None:
                continue  # campo no comparable en uno de los lados
            campos[campo].comparados += 1
            if ev == cv:
                campos[campo].correctos += 1
            else:
                diffs.append(f"{campo}: esperado={ev!r} extraído={cv!r}")
        if diffs:
            discordancias.append(f"{e['nombre']} → " + "; ".join(diffs))

    nombre_recall = emparejados / len(esperado) if esperado else 0.0
    return ResultadoEval(
        n_candidato=len(candidato),
        n_esperado=len(esperado),
        n_emparejados=emparejados,
        nombre_recall=nombre_recall,
        campos=campos,
        faltantes=faltantes,
        discordancias=discordancias,
    )


def aplicar_umbrales(r: ResultadoEval) -> dict[str, str]:
    """PASS/FAIL por umbral (ADR §D7). costo/duración: N/A si no hay sidecar (no reprueban en v1)."""
    def pf(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    veredictos = {
        "nombre_recall": pf(r.nombre_recall >= UMBRAL_NOMBRE_RECALL),
    }
    precio = r.campos["precio_venta"].exactitud
    veredictos["precio_exactitud"] = "N/A" if precio is None else pf(precio >= UMBRAL_PRECIO_EXACTITUD)
    veredictos["costo"] = "N/A" if r.costo_usd is None else pf(r.costo_usd <= UMBRAL_COSTO_USD)
    veredictos["duracion"] = (
        "N/A" if r.duracion_min is None else pf(r.duracion_min <= UMBRAL_DURACION_MIN)
    )
    veredictos["GLOBAL"] = "FAIL" if "FAIL" in veredictos.values() else "PASS"
    return veredictos


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------
def _pct(x: float | None) -> str:
    return "N/A" if x is None else f"{x * 100:.1f}%"


def reporte_markdown(r: ResultadoEval, *, map_model: str | None = None) -> str:
    lineas = [
        "# Eval dorado — onboarding mágico (ADR 0011 §D7)",
        "",
        f"- Productos candidato: **{r.n_candidato}** · ground truth: **{r.n_esperado}** · "
        f"emparejados: **{r.n_emparejados}**",
        f"- Cobertura: **{_pct(r.cobertura)}**",
        f"- map-model: `{map_model or 'N/A (v1 sin pipeline)'}`",
        "",
        "## Métricas por campo",
        "",
        "| Métrica | Valor | Umbral | Veredicto |",
        "|---|---|---|---|",
        f"| nombre (recall) | {_pct(r.nombre_recall)} | ≥{_pct(UMBRAL_NOMBRE_RECALL)} | "
        f"{r.veredictos.get('nombre_recall', '—')} |",
        f"| precio_venta (exacto) | {_pct(r.campos['precio_venta'].exactitud)} | "
        f"≥{_pct(UMBRAL_PRECIO_EXACTITUD)} | {r.veredictos.get('precio_exactitud', '—')} |",
        f"| unidad_medida | {_pct(r.campos['unidad_medida'].exactitud)} | — | — |",
        f"| permite_fraccion | {_pct(r.campos['permite_fraccion'].exactitud)} | — | — |",
        f"| fracciones | {_pct(r.campos['fracciones'].exactitud)} | — | — |",
        f"| escalonado | {_pct(r.campos['escalonado'].exactitud)} | — | — |",
        "",
        f"| costo (USD) | {r.costo_usd if r.costo_usd is not None else 'N/A'} | "
        f"≤{UMBRAL_COSTO_USD} | {r.veredictos.get('costo', '—')} |",
        f"| duración (min) | {r.duracion_min if r.duracion_min is not None else 'N/A'} | "
        f"≤{UMBRAL_DURACION_MIN} | {r.veredictos.get('duracion', '—')} |",
        "",
        f"## Veredicto global: **{r.veredictos.get('GLOBAL', '—')}**",
        "",
        f"## Faltantes ({len(r.faltantes)}) — ground truth sin pareja",
        *([f"- {m}" for m in r.faltantes] or ["- (ninguno)"]),
        "",
        f"## Discordancias ({len(r.discordancias)}) — emparejados con campos distintos",
        *([f"- {d}" for d in r.discordancias] or ["- (ninguna)"]),
        "",
    ]
    return "\n".join(lineas)


def reporte_json(r: ResultadoEval, *, map_model: str | None = None) -> dict:
    return {
        "n_candidato": r.n_candidato,
        "n_esperado": r.n_esperado,
        "n_emparejados": r.n_emparejados,
        "cobertura": r.cobertura,
        "nombre_recall": r.nombre_recall,
        "map_model": map_model,
        "campos": {k: {"comparados": v.comparados, "correctos": v.correctos, "exactitud": v.exactitud}
                   for k, v in r.campos.items()},
        "costo_usd": r.costo_usd,
        "duracion_min": r.duracion_min,
        "veredictos": r.veredictos,
        "faltantes": r.faltantes,
        "discordancias": r.discordancias,
    }


def _cargar_metricas(path: str | None) -> tuple[float | None, float | None]:
    """Sidecar opcional de métricas del pipeline (v2): {costo_usd, duracion_min}. Ausente → (None, None)."""
    if not path:
        return None, None
    datos = json.loads(Path(path).read_text(encoding="utf-8"))
    return datos.get("costo_usd"), datos.get("duracion_min")


def evaluar(
    manifiesto_path: str, ground_truth_path: str, *,
    umbral_nombre: float = 0.9, metricas_path: str | None = None,
) -> ResultadoEval:
    """Orquesta carga + comparación + umbrales. Devuelve el ResultadoEval con veredictos resueltos."""
    candidato = cargar_productos(manifiesto_path)
    esperado = cargar_productos(ground_truth_path)
    r = comparar(candidato, esperado, umbral_nombre=umbral_nombre)
    r.costo_usd, r.duracion_min = _cargar_metricas(metricas_path)
    r.veredictos = aplicar_umbrales(r)
    return r


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Eval dorado del onboarding mágico (ADR 0011 §D7).")
    parser.add_argument("--manifiesto", required=True, help="Manifiesto candidato (YAML/JSON con pack pos)")
    parser.add_argument("--ground-truth", required=True, help="Catálogo real esperado (CSV o YAML)")
    parser.add_argument("--salida", help="Prefijo de salida: escribe <salida>.md y <salida>.json")
    parser.add_argument("--umbral-nombre", type=float, default=0.9, help="Umbral de match fuzzy (0..1)")
    parser.add_argument("--metricas", help="Sidecar JSON con {costo_usd, duracion_min} del pipeline (v2)")
    parser.add_argument(
        "--map-model",
        help="Knob de costo (ADR §D7): modelo del map para correr el pipeline (v2). En v1 solo se anota.",
    )
    args = parser.parse_args(argv)

    try:
        r = evaluar(
            args.manifiesto, args.ground_truth,
            umbral_nombre=args.umbral_nombre, metricas_path=args.metricas,
        )
    except Exception as exc:  # noqa: BLE001 — CLI: cualquier fallo → exit!=0 con mensaje claro
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    md = reporte_markdown(r, map_model=args.map_model)
    print(md)
    if args.salida:
        Path(f"{args.salida}.md").write_text(md, encoding="utf-8")
        Path(f"{args.salida}.json").write_text(
            json.dumps(reporte_json(r, map_model=args.map_model), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    # Exit code = veredicto: 0 PASS, 1 FAIL (script repetible / comparable entre corridas).
    return 0 if r.veredictos.get("GLOBAL") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
