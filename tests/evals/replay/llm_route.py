"""Ruta LLM del replay (ADR 0024): evalúa la elección de herramienta del agente WhatsApp por pack.

El replay clásico cubre el camino BYPASS (ventas deterministas). Esta ruta cubre el 40% que cae al
modelo en el canal público: ¿ante la frase del cliente el agente llama la herramienta correcta del pack
(cotizaciones / cobranza / pedidos) o escala a un humano cuando corresponde?

Diseño testeable sin gastar dinero (los tests NUNCA llaman a una API real):
  - El proveedor LLM es INYECTADO. En CI/tests se pasa un proveedor scripteado (fake); una corrida
    manual/offline puede pasar el proveedor real (con su key). Esta capa no resuelve credenciales.
  - Las herramientas de dominio NO se ejecutan: un `_EjecutorStub` registra qué tool pidió el modelo y
    devuelve un `Resultado` neutro, para aislar la DECISIÓN del modelo de la ejecución (que ya está
    cubierta por los tests de cada pack). Se reusa el bucle REAL `apps.wa.agent.correr_bucle`.
  - LLM-as-judge para el texto libre: `Juez` es un puerto OPT-IN; el default `JuezDesactivado` no evalúa
    (ni red ni costo). Un juez real (otro proveedor) solo se cablea con `--judge` + key, fuera de CI.

Formato de cada caso (JSONL), p. ej.:
    {"frase": "¿a cómo el cemento?", "espera_tool": "cotizar_producto", "categoria": "cotizaciones"}
    {"frase": "quiero hablar con una persona", "espera": "handoff", "categoria": "handoff"}
    {"frase": "gracias, eso es todo", "espera": "texto", "categoria": "cortesia"}

  - espera_tool: nombre de la herramienta que el agente DEBE llamar (implica espera="tool").
  - espera: "tool" | "handoff" | "texto" (default "tool" si hay espera_tool, si no "texto").
  - categoria: etiqueta para segmentar el reporte (reusa la agregación del replay de bypass).
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Protocol

from ai.envelope import Contexto, Resultado
from apps.wa.agent import correr_bucle, exponer_runtime
from core.llm.factory import LLMResuelto

# Capacidades de un tenant "todo terreno" para exponer los packs de cara al público en el eval.
_CAPACIDADES_EVAL = frozenset({
    "canal_whatsapp", "pack_agenda", "pack_faq", "pack_ventas", "pack_cobranza",
    "pack_pedidos", "pack_postventa", "pack_reservas",
})

# Herramienta transversal de escalamiento a humano (pack handoff).
_TOOL_HANDOFF = "escalar_humano"


@dataclass(slots=True)
class ResultadoLLM:
    """Lo observado en un turno del eval: qué tools pidió el modelo y con qué texto cerró."""

    tools_llamadas: list[str] = field(default_factory=list)
    texto: str = ""


class _EjecutorStub:
    """Ejecutor de herramientas de juguete: registra el nombre y devuelve un `Resultado` neutro.

    Aísla la decisión del modelo de la ejecución de dominio (probada en cada pack). Así el bucle avanza
    a un texto final sin tocar la BD ni servicios reales.
    """

    def __init__(self) -> None:
        self.llamadas: list[str] = []

    async def __call__(self, tool_call: Any, ctx: Contexto, deps: Any) -> Resultado:
        self.llamadas.append(tool_call.name)
        return Resultado(data={}, resumen="ok")


class Juez(Protocol):
    """Puerto del LLM-as-judge (opt-in). `evaluar` da un veredicto para el texto libre del agente."""

    async def evaluar(self, frase: str, texto: str, caso: dict) -> "VeredictoJuez": ...


@dataclass(frozen=True, slots=True)
class VeredictoJuez:
    aprobado: bool
    motivo: str = ""


class JuezDesactivado:
    """Default: NO juzga (ni red ni costo). Todo caso pasa el juez (se evalúa solo la tool)."""

    async def evaluar(self, frase: str, texto: str, caso: dict) -> VeredictoJuez:
        return VeredictoJuez(aprobado=True, motivo="juez desactivado")


def _espera(caso: dict) -> str:
    if caso.get("espera_tool"):
        return "tool"
    return caso.get("espera", "texto")


async def correr_caso(
    caso: dict, proveedor: LLMResuelto, *, capacidades: frozenset[str] = _CAPACIDADES_EVAL
) -> ResultadoLLM:
    """Pasa una frase por el bucle REAL del agente con un ejecutor stub; devuelve tools + texto final."""
    ctx = Contexto(
        tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
        cliente_telefono="573000000000", capacidades=capacidades,
    )
    ejecutor = _EjecutorStub()
    texto = await correr_bucle(
        proveedor=proveedor,
        system="(eval) asistente de prueba",
        tools=exponer_runtime(ctx),
        ctx=ctx,
        deps=None,                       # el ejecutor stub no usa deps
        historial=[],
        texto=caso["frase"],
        ejecutar=ejecutor,
    )
    return ResultadoLLM(tools_llamadas=list(ejecutor.llamadas), texto=texto)


def evaluar_llm(caso: dict, res: ResultadoLLM, veredicto: VeredictoJuez) -> tuple[str, str]:
    """Clasifica un caso de la ruta LLM. `outcome='ok'` es acierto; el resto son fallos.

    - espera="tool": la tool esperada debe estar entre las llamadas.
    - espera="handoff": el agente debe haber llamado `escalar_humano`.
    - espera="texto": NO debe llamar herramientas y (si hay juez activo) el texto debe aprobar.
    """
    espera = _espera(caso)
    if espera == "tool":
        objetivo = caso["espera_tool"]
        if objetivo in res.tools_llamadas:
            return "ok", ""
        return "fail_tool", f"esperaba tool {objetivo!r}, llamó {res.tools_llamadas}"
    if espera == "handoff":
        if _TOOL_HANDOFF in res.tools_llamadas:
            return "ok", ""
        return "fail_handoff", f"esperaba handoff, llamó {res.tools_llamadas}"
    # espera == "texto": sin herramientas + veredicto del juez (si activo)
    if res.tools_llamadas:
        return "fail_tool_indebida", f"no esperaba tools, llamó {res.tools_llamadas}"
    if not veredicto.aprobado:
        return "fail_juez", veredicto.motivo
    return "ok", ""


async def correr_llm(
    corpus: list[dict], proveedor: LLMResuelto, *, juez: Juez | None = None
) -> list[dict]:
    """Pasa cada caso por la ruta LLM y devuelve filas compatibles con `replay.agregar`/`imprimir_reporte`."""
    juez = juez or JuezDesactivado()
    filas: list[dict] = []
    for caso in corpus:
        res = await correr_caso(caso, proveedor)
        espera = _espera(caso)
        veredicto = (
            await juez.evaluar(caso["frase"], res.texto, caso)
            if espera == "texto" else VeredictoJuez(True)
        )
        outcome, detalle = evaluar_llm(caso, res, veredicto)
        filas.append({
            "frase": caso["frase"],
            "categoria": caso.get("categoria", "sin_categoria"),
            "espera": espera,
            "got": res.tools_llamadas[0] if res.tools_llamadas else "texto",
            "outcome": outcome,
            "detalle": detalle,
        })
    return filas


def cargar_corpus_llm(path: str) -> list[dict]:
    """Lee el corpus JSONL de la ruta LLM (mismo formato tolerante que el replay de bypass)."""
    import json

    casos: list[dict] = []
    for n, linea in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        s = linea.strip()
        if not s or s.startswith("#"):
            continue
        try:
            casos.append(json.loads(s))
        except json.JSONDecodeError as e:  # pragma: no cover - error de uso
            raise SystemExit(f"corpus LLM inválido en {path}:{n}: {e}") from e
    return casos
