#!/usr/bin/env python3
"""Runner de replay: pasa un corpus de mensajes por el bot REAL y mide el acierto.

Ejecuta cada frase contra el runtime de producción del agente (`ai.bypass.Bypass.intentar` →
`ai.dispatcher.Dispatcher.ejecutar`) usando el harness en memoria de `tests/evals/_harness.py`
(servicios de dominio sin BD; despachador real). Captura el `ToolCall` emitido (la venta registrada),
lo compara con lo esperado y reporta el acierto global y por categoría.

Ruta cubierta hoy: BYPASS (camino determinista, ~60% de ventas). La ruta LLM (el 40% restante)
requiere tenant sembrado + clave de proveedor; ver README.md (Fase 0 LLM).

Uso:
    # Corpus semilla (corre hoy, sin BD ni LLM):
    python -m tests.evals.replay.replay --corpus tests/evals/replay/corpus_seed.jsonl

    # Corpus real contra el catálogo real exportado del tenant:
    python -m tests.evals.replay.replay \
        --corpus corpus_puntorojo.jsonl --catalogo catalogo_puntorojo.json --json-out reporte.json

Formato de cada línea del corpus (JSONL):
    {"frase": "3 vinilo", "espera": "venta", "items": [[7, "3"]], "total": "60000.00",
     "categoria": "entero"}
    {"frase": "3 cemento", "espera": "defiere", "categoria": "escalonado"}

  - espera: "venta" | "defiere" | "pregunta" | "error"
  - items:  lista de [producto_id, cantidad] (cantidad como string Decimal). Solo si espera="venta".
  - total:  total esperado (string Decimal), opcional. Tolerancia 1% o $1 (la del riel de precio).
  - categoria: etiqueta libre para segmentar el reporte.

Código de salida: 0 si acierto_global >= umbral y 0 peligrosos; 1 si no; 2 para errores de uso.

Las importaciones del repo se hacen DENTRO de las funciones a propósito: así el módulo se puede
importar (y `--help`) sin tener instaladas todas las dependencias del proyecto, y la lógica de
agregación/reporte se puede testear con solo stdlib.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from decimal import Decimal

# El repo importa por nombre de paquete absoluto (ai, modules, tests...). Aseguramos la raíz en sys.path
# para que `python tests/evals/replay/replay.py ...` funcione igual que `python -m ...`.
_REPO = pathlib.Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Dos niveles de error al registrar una venta (decisión del owner: separar lo alarmante del ruido):
#   PELIGROSO   = sobre-registro grosero (total >= 10x lo esperado → "millones") o registro indebido
#                 (registró cuando debía deferir/preguntar). Es lo que el bot NUNCA debe hacer → meta 0.
#   EQUIVOCACION= registró con items/total fuera de tolerancia pero SIN ser grosero (deriva de precio
#                 histórico, artefacto de reconstrucción, variante). Reporta el error sin alarmar.
# El acierto sigue siendo estricto (misma tolerancia); estos niveles solo clasifican los NO-aciertos.
_PELIGROSOS = ("fail_peligroso", "fail_registro_indebido")
_EQUIVOCACIONES = ("fail_equivocacion",)
_FACTOR_PELIGRO = Decimal("10")  # total emitido >= 10x el esperado = sobre-registro grosero


# ─────────────────────────────────────────────────────────────────────────────
# Carga de corpus y catálogo (stdlib; las clases del repo se importan perezosamente)
# ─────────────────────────────────────────────────────────────────────────────
def cargar_corpus(path: str) -> list[dict]:
    """Lee un JSONL (ignora líneas en blanco y las que empiezan con '#')."""
    casos: list[dict] = []
    for n, linea in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        s = linea.strip()
        if not s or s.startswith("#"):
            continue
        try:
            casos.append(json.loads(s))
        except json.JSONDecodeError as e:
            raise SystemExit(f"corpus inválido en {path}:{n}: {e}") from e
    return casos


def _dec(v) -> "Decimal | None":
    return None if v is None else Decimal(str(v))


def productos_desde_json(path: str):
    """catalogo.json (formato de extraer_catalogo.py) → tupla de ProductoPrecio del dominio."""
    from modules.inventario.precios import FraccionPrecio
    from modules.ventas.service import ProductoPrecio

    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    productos = []
    for p in data:
        fracciones = tuple(
            FraccionPrecio(decimal=_dec(d), precio_total=_dec(t)) for d, t in p.get("fracciones", [])
        )
        productos.append(
            ProductoPrecio(
                id=int(p["id"]),
                nombre=p["nombre"],
                precio_venta=_dec(p["precio_venta"]),
                iva=int(p.get("iva", 0)),
                activo=bool(p.get("activo", True)),
                precio_umbral=_dec(p.get("precio_umbral")),
                precio_bajo_umbral=_dec(p.get("precio_bajo_umbral")),
                precio_sobre_umbral=_dec(p.get("precio_sobre_umbral")),
                fracciones=fracciones,
                unidad_medida=p.get("unidad_medida", "Unidad"),
            )
        )
    return tuple(productos)


# ─────────────────────────────────────────────────────────────────────────────
# Ejecución de un caso por la ruta bypass
# ─────────────────────────────────────────────────────────────────────────────
def _clasificar(res) -> str:
    """Respuesta del despachador → etiqueta estable. None = el bypass defirió al modelo."""
    from ai.envelope import ErrorTool, Resultado
    from ai.rieles import Confirmar, Preguntar

    if res is None:
        return "defiere"
    if isinstance(res, Resultado):
        return "venta"
    if isinstance(res, (Confirmar, Preguntar)):
        return "pregunta"
    if isinstance(res, ErrorTool):
        return "error"
    return "desconocido"


def _total_ok(total_emitido: Decimal, total_esp: Decimal) -> bool:
    tolerancia = max(total_esp * Decimal("0.01"), Decimal("1"))
    return abs(total_emitido - total_esp) <= tolerancia


def _agrupar_items(items) -> dict:
    """[(producto_id, cantidad), ...] -> {producto_id: suma_cantidad}. Ignora el orden y la división
    de una misma cantidad en componentes (p. ej. mixta '2 1/2' como [2, 0.5])."""
    agrupado: dict = {}
    for pid, cantidad in items:
        agrupado[pid] = agrupado.get(pid, Decimal(0)) + cantidad
    return agrupado


def _nivel_sobre_registro(total_emitido: Decimal, total_esp: Decimal) -> str:
    """Clasifica un total fuera de tolerancia: grosero (>=10x → 'millones') vs equivocación suave."""
    if total_esp > 0 and total_emitido >= _FACTOR_PELIGRO * total_esp:
        return "fail_peligroso"
    return "fail_equivocacion"


def _evaluar(caso: dict, got: str, header) -> tuple[str, str]:
    """Devuelve (outcome, detalle). outcome='ok' es acierto; los demás son fallos.

    Los NO-aciertos al registrar se clasifican en dos niveles (ver _PELIGROSOS/_EQUIVOCACIONES):
    sobre-registro grosero (>=10x) o registro indebido = PELIGROSO; el resto = EQUIVOCACION (suave).
    """
    espera = caso.get("espera", "venta")
    if espera == "venta":
        if got != "venta":
            return "fail_no_registro", f"esperaba venta, obtuvo '{got}' (cobertura: la tomaría el modelo)"
        emitidos = [(linea.producto_id, linea.cantidad) for linea in header.lineas]
        esperados = [(int(pid), Decimal(str(c))) for pid, c in caso.get("items", [])]
        # Comparar por cantidad AGRUPADA por producto: "2 1/2" emitido como [2, 0.5] equivale a [2.5].
        if _agrupar_items(emitidos) != _agrupar_items(esperados):
            return "fail_equivocacion", f"items emitidos {emitidos} != esperados {esperados}"
        if caso.get("total") is not None and not _total_ok(header.total, Decimal(str(caso["total"]))):
            total_esp = Decimal(str(caso["total"]))
            return (
                _nivel_sobre_registro(header.total, total_esp),
                f"total emitido {header.total} != esperado {caso['total']}",
            )
        return "ok", ""
    # espera defiere / pregunta / error
    if got == "venta":
        return "fail_registro_indebido", "registró una venta cuando debía deferir/preguntar (PELIGROSO)"
    if got == espera:
        return "ok", ""
    return "fail_clasificacion", f"esperaba '{espera}', obtuvo '{got}'"


async def correr(corpus: list[dict], productos) -> list[dict]:
    """Pasa cada frase por un harness fresco (sin estado compartido) y devuelve las filas de resultado."""
    from tests.evals._harness import construir, ctx_eval

    filas: list[dict] = []
    for i, caso in enumerate(corpus):
        harness = construir(productos)                       # harness nuevo por caso: sin fuga de estado
        ctx = ctx_eval(key=f"replay-{i}")                    # idempotency_key único por caso
        res = await harness.bypass.intentar(caso["frase"], ctx, harness.recursos)
        got = _clasificar(res)
        header = harness.ventas_repo.ultimo_header if got == "venta" else None
        outcome, detalle = _evaluar(caso, got, header)
        filas.append(
            {
                "frase": caso["frase"],
                "categoria": caso.get("categoria", "sin_categoria"),
                "espera": caso.get("espera", "venta"),
                "got": got,
                "outcome": outcome,
                "detalle": detalle,
            }
        )
    return filas


# ─────────────────────────────────────────────────────────────────────────────
# Agregación y reporte (puras: testeable con solo stdlib)
# ─────────────────────────────────────────────────────────────────────────────
def agregar(filas: list[dict]) -> tuple[dict, dict]:
    cats: dict[str, dict] = {}
    glob = {"n": 0, "ok": 0, "peligrosos": 0, "equivocaciones": 0, "resolvio": 0, "defirio": 0}
    for f in filas:
        c = cats.setdefault(f["categoria"], {"n": 0, "ok": 0, "peligrosos": 0, "equivocaciones": 0})
        c["n"] += 1
        glob["n"] += 1
        if f["outcome"] == "ok":
            c["ok"] += 1
            glob["ok"] += 1
        if f["outcome"] in _PELIGROSOS:
            c["peligrosos"] += 1
            glob["peligrosos"] += 1
        if f["outcome"] in _EQUIVOCACIONES:
            c["equivocaciones"] += 1
            glob["equivocaciones"] += 1
        if f["got"] == "venta":
            glob["resolvio"] += 1
        elif f["got"] == "defiere":
            glob["defirio"] += 1
    return cats, glob


def _pct(num: int, den: int) -> str:
    return f"{(100.0 * num / den):5.1f}%" if den else "  n/a"


def imprimir_reporte(
    cats: dict, glob: dict, filas: list[dict], umbral: float, *, titulo: str = "ruta bypass"
) -> None:
    print(f"\n== REPLAY · acierto del bot ({titulo}) ==\n")
    print(f"  {'categoría':<22} {'n':>4} {'ok':>4} {'acierto':>8} {'peligro':>8} {'equivoc':>8}")
    print(f"  {'-' * 22} {'-' * 4} {'-' * 4} {'-' * 8} {'-' * 8} {'-' * 8}")
    for nombre in sorted(cats):
        d = cats[nombre]
        print(f"  {nombre:<22} {d['n']:>4} {d['ok']:>4} {_pct(d['ok'], d['n']):>8} "
              f"{d['peligrosos']:>8} {d['equivocaciones']:>8}")

    n, ok = glob["n"], glob["ok"]
    print(f"\n  GLOBAL: {ok}/{n} acierto = {_pct(ok, n)}")
    print(f"  cobertura bypass: {glob['resolvio']} registradas / {glob['defirio']} diferidas al modelo")
    print(f"  PELIGROSOS (sobre-registro grosero o indebido): {glob['peligrosos']}")
    print(f"  equivocaciones (total/items fuera de tolerancia, no grosero): {glob['equivocaciones']}")

    fallos = [f for f in filas if f["outcome"] != "ok"]
    if fallos:
        print("\n  fallos:")
        for f in fallos:
            marca = "⚠ " if f["outcome"] in _PELIGROSOS else "  "
            print(f"   {marca}[{f['outcome']}] {f['frase']!r} :: {f['detalle']}")

    acierto = ok / n if n else 0.0
    ok_umbral = acierto >= umbral and glob["peligrosos"] == 0
    print(f"\n  veredicto: acierto {_pct(ok, n)} vs umbral {umbral * 100:.1f}% "
          f"y {glob['peligrosos']} peligrosos → {'PASA' if ok_umbral else 'FALLA'}\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Replay de corpus contra el bot de ventas (acierto/paridad).")
    ap.add_argument("--corpus", required=True, help="ruta al corpus JSONL")
    ap.add_argument("--catalogo", default=None,
                    help="catalogo.json del tenant (de extraer_catalogo.py). Si falta, usa el catálogo del harness.")
    ap.add_argument("--route", choices=["bypass", "llm"], default="bypass",
                    help="ruta a evaluar: 'bypass' (determinista) o 'llm' (elección de tool del agente WA).")
    ap.add_argument("--umbral", type=float, default=0.95, help="acierto mínimo para salir con código 0")
    ap.add_argument("--json-out", default=None, help="vuelca el reporte detallado a un JSON (para comparar paridad)")
    ap.add_argument("--judge", action="store_true",
                    help="ruta llm: activa el LLM-as-judge del texto libre (opt-in; requiere key). Off por defecto.")
    args = ap.parse_args(argv)

    # Consolas/pipes Windows cp1252 no codifican el ⚠ ni los emojis del corpus: sin esto, `print`
    # revienta con UnicodeEncodeError y el replay sale con código 1 aunque haya pasado.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if args.route == "llm":
        return _main_llm(args)

    corpus = cargar_corpus(args.corpus)
    sin_etiqueta = sum(1 for c in corpus if c.get("espera") == "?")
    corpus = [c for c in corpus if c.get("espera") != "?"]
    if sin_etiqueta:
        print(f"  {sin_etiqueta} casos sin etiqueta (espera='?') omitidos en la ruta bypass.")
    if not corpus:
        print(f"corpus sin casos etiquetados: {args.corpus}")
        return 2

    productos = productos_desde_json(args.catalogo) if args.catalogo else _catalogo_harness()
    filas = asyncio.run(correr(corpus, productos))
    cats, glob = agregar(filas)
    imprimir_reporte(cats, glob, filas, args.umbral)

    if args.json_out:
        reporte = {"global": glob, "categorias": cats, "filas": filas, "umbral": args.umbral}
        pathlib.Path(args.json_out).write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  reporte JSON → {args.json_out}\n")

    acierto = glob["ok"] / glob["n"] if glob["n"] else 0.0
    return 0 if (acierto >= args.umbral and glob["peligrosos"] == 0) else 1


def _catalogo_harness():
    from tests.evals._harness import PRODUCTOS
    return PRODUCTOS


def _proveedor_real_o_none():
    """Proveedor LLM de plataforma (para corridas manuales). None si no hay key configurada.

    La ruta LLM NUNCA se ejecuta con proveedor real en CI: los tests llaman al harness
    (`tests/evals/replay/llm_route.py`) con un proveedor scripteado. Aquí, en el CLI manual, se
    resuelve el proveedor por defecto de plataforma con su key del entorno.
    """
    from core.config import get_settings
    from core.llm.factory import LLMResuelto, PlataformaLLM
    from core.llm import registry

    plataforma = PlataformaLLM.desde_settings(get_settings())
    key = plataforma.keys.get(plataforma.provider)
    if not key:
        return None
    clase = registry.obtener_clase(plataforma.provider)
    return LLMResuelto(
        provider=clase(api_key=key), model=plataforma.model_orquestador,
        provider_nombre=plataforma.provider,
    )


def _main_llm(args) -> int:
    """Ruta LLM del CLI (manual): corre el corpus WA contra el proveedor real de plataforma.

    Requiere una key de proveedor en el entorno (nunca se corre con API real en CI). El LLM-as-judge
    es opt-in con `--judge`; sin él, el juez está desactivado (solo se evalúa la elección de tool).
    """
    import asyncio

    from tests.evals.replay.llm_route import JuezDesactivado, cargar_corpus_llm, correr_llm

    corpus = cargar_corpus_llm(args.corpus)
    if not corpus:
        print(f"corpus LLM sin casos: {args.corpus}")
        return 2
    proveedor = _proveedor_real_o_none()
    if proveedor is None:
        print("La ruta 'llm' del CLI requiere una key de proveedor en el entorno (ANTHROPIC/OPENAI). "
              "Los tests la ejercen con un proveedor scripteado (ver tests/evals/replay/llm_route.py).")
        return 2
    juez = JuezDesactivado()
    if args.judge:
        print("  --judge pedido pero el juez real aún no se cablea al CLI; se usa el juez desactivado.")
    filas = asyncio.run(correr_llm(corpus, proveedor, juez=juez))
    cats, glob = agregar(filas)
    imprimir_reporte(cats, glob, filas, args.umbral, titulo="ruta llm (agente WA)")
    if args.json_out:
        reporte = {"global": glob, "categorias": cats, "filas": filas, "umbral": args.umbral}
        pathlib.Path(args.json_out).write_text(
            json.dumps(reporte, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    acierto = glob["ok"] / glob["n"] if glob["n"] else 0.0
    return 0 if (acierto >= args.umbral and glob["peligrosos"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
