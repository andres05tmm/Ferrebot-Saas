"""Providers: traducen el catálogo canónico al formato del vendor y normalizan la respuesta.

El test clave (swap) demuestra la abstracción: el mismo turno resuelto por Claude o por OpenAI
produce el MISMO ToolCall canónico — cambiar de IA es configuración, no reescritura.
"""
from core.llm.base import Message, ToolCall, ToolSpec
from core.llm.providers.claude import ClaudeProvider
from core.llm.providers.openai import OpenAIProvider

_TOOL = ToolSpec(
    name="registrar_venta",
    description="Registra una venta",
    parameters={
        "type": "object",
        "properties": {"producto": {"type": "string"}},
        "required": ["producto"],
    },
)

# Respuestas crudas tal como las devuelve cada vendor para el MISMO turno.
_CLAUDE_RAW = {
    "model": "claude-haiku",
    "stop_reason": "tool_use",
    "content": [
        {"type": "text", "text": "Registro la venta"},
        {"type": "tool_use", "id": "toolu_1", "name": "registrar_venta",
         "input": {"producto": "martillo", "cantidad": 2}},
    ],
    "usage": {"input_tokens": 10, "output_tokens": 5},
}
_OPENAI_RAW = {
    "model": "gpt-4o-mini",
    "choices": [{
        "finish_reason": "tool_calls",
        "message": {
            "content": "Registro la venta",
            "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "registrar_venta",
                             "arguments": '{"producto": "martillo", "cantidad": 2}'},
            }],
        },
    }],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
}


def test_claude_traduce_tools():
    out = ClaudeProvider(api_key="x").traducir_tools([_TOOL])
    assert out == [{
        "name": "registrar_venta",
        "description": "Registra una venta",
        "input_schema": _TOOL.parameters,
    }]


def test_openai_traduce_tools():
    out = OpenAIProvider(api_key="x").traducir_tools([_TOOL])
    assert out == [{
        "type": "function",
        "function": {
            "name": "registrar_venta",
            "description": "Registra una venta",
            "parameters": _TOOL.parameters,
        },
    }]


def test_claude_normaliza_respuesta():
    resp = ClaudeProvider(api_key="x").parsear_respuesta(_CLAUDE_RAW)
    assert resp.text == "Registro la venta"
    assert resp.model == "claude-haiku"
    assert resp.tool_calls == [
        ToolCall(id="toolu_1", name="registrar_venta",
                 arguments={"producto": "martillo", "cantidad": 2})
    ]


def test_openai_normaliza_respuesta():
    resp = OpenAIProvider(api_key="x").parsear_respuesta(_OPENAI_RAW)
    assert resp.text == "Registro la venta"
    assert resp.tool_calls == [
        ToolCall(id="call_1", name="registrar_venta",
                 arguments={"producto": "martillo", "cantidad": 2})
    ]


async def test_generate_usa_cliente_inyectado():
    capturado = {}

    async def _fake_client(payload):
        capturado["payload"] = payload
        return _CLAUDE_RAW

    provider = ClaudeProvider(api_key="x", client=_fake_client)
    resp = await provider.generate(
        messages=[Message(role="user", content="2 martillo")], tools=[_TOOL], model="claude-haiku"
    )
    assert resp.tool_calls[0].name == "registrar_venta"
    assert capturado["payload"]["model"] == "claude-haiku"
    assert capturado["payload"]["tools"][0]["name"] == "registrar_venta"


def test_swap_mismo_toolcall_canonico():
    claude = ClaudeProvider(api_key="x").parsear_respuesta(_CLAUDE_RAW)
    openai = OpenAIProvider(api_key="x").parsear_respuesta(_OPENAI_RAW)
    canonico = lambda resp: [(tc.name, tc.arguments) for tc in resp.tool_calls]
    assert canonico(claude) == canonico(openai)
    assert canonico(claude) == [("registrar_venta", {"producto": "martillo", "cantidad": 2})]
