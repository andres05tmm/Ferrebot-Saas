"""Prompt caching de Claude + captura de tokens de caché en la medición (ADR 0024).

- El payload de Claude marca el PREFIJO estable (última tool + bloque system) con `cache_control`
  efímero, sin romper el prefijo ni alterar `traducir_tools` (que sigue puro).
- `ProveedorMedido` emite el evento `llm_uso` con `cache_read`/`cache_creation` y latencia, y NO
  duplica el conteo del ledger (input/output siguen igual).
"""
from core.llm.base import LLMResponse, Message, ToolSpec
from core.llm.medicion import ProveedorMedido
from core.llm.providers.claude import ClaudeProvider

_TOOL_A = ToolSpec(name="a", description="A", parameters={"type": "object", "properties": {}})
_TOOL_B = ToolSpec(name="b", description="B", parameters={"type": "object", "properties": {}})


def test_payload_marca_cache_en_ultima_tool_y_system():
    prov = ClaudeProvider(api_key="x")
    payload = prov._payload(
        [Message(role="user", content="hola")], [_TOOL_A, _TOOL_B], "claude-x",
        system="eres un asistente", extra={},
    )
    # cache_control SOLO en la última tool (breakpoint del prefijo de herramientas).
    assert "cache_control" not in payload["tools"][0]
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    # system como bloque de texto con cache_control efímero.
    assert payload["system"] == [
        {"type": "text", "text": "eres un asistente", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    ]


def test_traducir_tools_sigue_puro_sin_cache_control():
    out = ClaudeProvider(api_key="x").traducir_tools([_TOOL_A])
    assert out == [{"name": "a", "description": "A", "input_schema": _TOOL_A.parameters}]
    assert "cache_control" not in out[0]


def test_sin_tools_no_falla_y_no_agrega_cache():
    payload = ClaudeProvider(api_key="x")._payload(
        [Message(role="user", content="hola")], [], "claude-x", system=None, extra={}
    )
    assert payload["tools"] == []


# ------------------------------ medición ----------------------------------
class _FakeLLM:
    nombre = "claude"
    api_key = "k"

    def __init__(self, resp):
        self._resp = resp

    async def generate(self, **kw):
        return self._resp


class _FakeCostos:
    def __init__(self):
        self.llamadas = []

    async def acumular(self, *, fecha, modelo, tokens_in, tokens_out):
        self.llamadas.append((tokens_in, tokens_out))


async def test_medicion_no_duplica_ledger_con_tokens_de_cache():
    # usage con cache: el ledger sigue contando input/output; la caché va al evento de métrica.
    resp = LLMResponse(
        text="ok",
        usage={
            "input_tokens": 12, "output_tokens": 4,
            "cache_read_input_tokens": 900, "cache_creation_input_tokens": 100,
        },
    )
    costos = _FakeCostos()
    medido = ProveedorMedido(_FakeLLM(resp), costos)
    r = await medido.generate(messages=[], tools=[], model="claude-x", system=None)
    assert r.text == "ok"
    assert costos.llamadas == [(12, 4)]        # el ledger NO suma los tokens de caché


async def test_medicion_best_effort_con_cache_ausente():
    resp = LLMResponse(text="ok", usage={"input_tokens": 5, "output_tokens": 1})
    costos = _FakeCostos()
    medido = ProveedorMedido(_FakeLLM(resp), costos)
    await medido.generate(messages=[], tools=[], model="m", system=None)
    assert costos.llamadas == [(5, 1)]
