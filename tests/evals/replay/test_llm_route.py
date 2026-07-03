"""Ruta LLM del replay (ADR 0024): harness de elección de herramienta del agente WA.

Los tests NUNCA llaman a una API real: el proveedor es un fake scripteado y el juez es opt-in (fake).
Se verifica el CABLEADO del harness (drive del bucle real + clasificación + juez), no la precisión de
un modelo real.
"""
from core.llm.base import LLMResponse, ToolCall
from core.llm.factory import LLMResuelto
from tests.evals.replay.llm_route import (
    VeredictoJuez,
    correr_caso,
    correr_llm,
    evaluar_llm,
)


class _ProviderScript:
    """Proveedor fake: emite las respuestas en orden. Sin red, sin costo."""

    nombre = "fake"
    api_key = "x"

    def __init__(self, respuestas: list[LLMResponse]) -> None:
        self._r = list(respuestas)

    async def generate(self, **kw) -> LLMResponse:
        return self._r.pop(0)


def _llm(respuestas) -> LLMResuelto:
    return LLMResuelto(provider=_ProviderScript(respuestas), model="m", provider_nombre="fake")


def _pide_tool(nombre: str) -> LLMResuelto:
    """Turno de 2 pasos: el modelo pide `nombre`, luego cierra con texto."""
    return _llm([
        LLMResponse(text=None, tool_calls=[ToolCall(id="c1", name=nombre, arguments={})]),
        LLMResponse(text="Listo, ¿algo más?", tool_calls=[]),
    ])


def _solo_texto(texto: str) -> LLMResuelto:
    return _llm([LLMResponse(text=texto, tool_calls=[])])


class _JuezSiempreRechaza:
    async def evaluar(self, frase, texto, caso) -> VeredictoJuez:
        return VeredictoJuez(aprobado=False, motivo="texto pobre")


# ------------------------------ correr_caso -------------------------------
async def test_correr_caso_captura_la_tool_llamada():
    res = await correr_caso({"frase": "¿a cómo el cemento?"}, _pide_tool("cotizar_producto"))
    assert res.tools_llamadas == ["cotizar_producto"]
    assert res.texto == "Listo, ¿algo más?"


# ------------------------------ evaluar_llm -------------------------------
def test_tool_correcta_es_acierto():
    caso = {"frase": "x", "espera_tool": "cotizar_producto"}
    res = type("R", (), {"tools_llamadas": ["cotizar_producto"], "texto": "ok"})()
    assert evaluar_llm(caso, res, VeredictoJuez(True))[0] == "ok"


def test_tool_incorrecta_falla():
    caso = {"frase": "x", "espera_tool": "cotizar_producto"}
    res = type("R", (), {"tools_llamadas": ["mi_saldo"], "texto": "ok"})()
    assert evaluar_llm(caso, res, VeredictoJuez(True))[0] == "fail_tool"


def test_handoff_esperado_y_cumplido():
    caso = {"frase": "x", "espera": "handoff"}
    res = type("R", (), {"tools_llamadas": ["escalar_humano"], "texto": "ok"})()
    assert evaluar_llm(caso, res, VeredictoJuez(True))[0] == "ok"


def test_texto_sin_tool_es_acierto_con_juez_desactivado():
    caso = {"frase": "gracias", "espera": "texto"}
    res = type("R", (), {"tools_llamadas": [], "texto": "¡Con gusto!"})()
    assert evaluar_llm(caso, res, VeredictoJuez(True))[0] == "ok"


def test_texto_con_tool_indebida_falla():
    caso = {"frase": "gracias", "espera": "texto"}
    res = type("R", (), {"tools_llamadas": ["cotizar_producto"], "texto": "x"})()
    assert evaluar_llm(caso, res, VeredictoJuez(True))[0] == "fail_tool_indebida"


def test_juez_puede_reprobar_el_texto_libre():
    caso = {"frase": "gracias", "espera": "texto"}
    res = type("R", (), {"tools_llamadas": [], "texto": "meh"})()
    assert evaluar_llm(caso, res, VeredictoJuez(False, "pobre"))[0] == "fail_juez"


# ------------------------------- correr_llm -------------------------------
async def test_correr_llm_acierto_de_tool():
    corpus = [{"frase": "¿a cómo?", "espera_tool": "cotizar_producto", "categoria": "cotizaciones"}]
    filas = await correr_llm(corpus, _pide_tool("cotizar_producto"))
    assert filas[0]["outcome"] == "ok"
    assert filas[0]["categoria"] == "cotizaciones"


async def test_correr_llm_texto_con_juez_opt_in_reprueba():
    corpus = [{"frase": "gracias", "espera": "texto", "categoria": "cortesia"}]
    filas = await correr_llm(corpus, _solo_texto("meh"), juez=_JuezSiempreRechaza())
    assert filas[0]["outcome"] == "fail_juez"


async def test_correr_llm_handoff():
    corpus = [{"frase": "quiero un humano", "espera": "handoff", "categoria": "handoff"}]
    filas = await correr_llm(corpus, _pide_tool("escalar_humano"))
    assert filas[0]["outcome"] == "ok"
