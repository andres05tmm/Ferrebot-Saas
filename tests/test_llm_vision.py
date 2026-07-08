"""Input de visión (imágenes) en la capa LLM — cambio ADITIVO (Fase 6, módulo 14).

Verifica dos cosas de la extensión multimodal:
  1. **Regresión (compat total):** un `Message` de solo texto (sin `images`) sigue traduciéndose a la
     forma histórica `{"role": ..., "content": <str>}` en ambos providers. El bot actual no cambia.
  2. **Construcción del mensaje con imagen** para Claude y para OpenAI, desde base64 y desde URL,
     con el provider MOCKEADO en `generate` (jamás una llamada real a la API, sin keys reales).

El flujo de negocio (recibo Bancolombia→JSON, bandeja de revisión, tools de obra) NO se prueba aquí:
es Fase 6 completa. Esto es solo la CAPACIDAD de visión en el adaptador.
"""
import pytest

from core.llm.base import ImageBlock, Message
from core.llm.providers.claude import ClaudeProvider
from core.llm.providers.openai import OpenAIProvider

# base64 diminuto (1x1 px) — el contenido no importa, solo que viaje intacto por el payload.
_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
_URL = "https://res.cloudinary.com/pim/image/upload/recibo.jpg"


# ------------------------------ ImageBlock (tipo canónico) ------------------------------

def test_imageblock_base64_ok():
    img = ImageBlock.desde_base64(_B64, "image/png")
    assert img.data == _B64
    assert img.media_type == "image/png"
    assert img.url is None


def test_imageblock_url_ok():
    img = ImageBlock.desde_url(_URL)
    assert img.url == _URL
    assert img.data is None


def test_imageblock_exige_una_sola_fuente():
    # ni base64 ni url
    with pytest.raises(ValueError):
        ImageBlock()
    # ambas a la vez
    with pytest.raises(ValueError):
        ImageBlock(media_type="image/png", data=_B64, url=_URL)


def test_imageblock_base64_exige_media_type():
    with pytest.raises(ValueError):
        ImageBlock(data=_B64)  # base64 sin media_type


# ------------------------------ Regresión: solo texto no cambia de forma ------------------------------

def test_claude_solo_texto_sigue_string_plano():
    msgs = [Message(role="user", content="2 martillos")]
    _system, cuerpo = ClaudeProvider(api_key="x").traducir_mensajes(msgs)
    assert cuerpo == [{"role": "user", "content": "2 martillos"}]  # string plano, no arreglo de bloques


def test_openai_solo_texto_sigue_string_plano():
    msgs = [Message(role="user", content="2 martillos")]
    cuerpo = OpenAIProvider(api_key="x").traducir_mensajes(msgs)
    assert cuerpo == [{"role": "user", "content": "2 martillos"}]


# ------------------------------ Claude: mensaje con imagen ------------------------------

def test_claude_imagen_base64_arma_bloques_texto_e_imagen():
    msgs = [Message(
        role="user",
        content="¿Cuánto es este recibo?",
        images=[ImageBlock.desde_base64(_B64, "image/jpeg")],
    )]
    _system, cuerpo = ClaudeProvider(api_key="x").traducir_mensajes(msgs)
    (mensaje,) = cuerpo
    assert mensaje["role"] == "user"
    bloques = mensaje["content"]
    # texto primero, luego la imagen
    assert bloques[0] == {"type": "text", "text": "¿Cuánto es este recibo?"}
    assert bloques[1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": _B64},
    }


def test_claude_imagen_url_usa_source_url():
    msgs = [Message(role="user", content="", images=[ImageBlock.desde_url(_URL)])]
    _system, cuerpo = ClaudeProvider(api_key="x").traducir_mensajes(msgs)
    bloques = cuerpo[0]["content"]
    # sin texto → solo el bloque de imagen (no un bloque text vacío)
    assert bloques == [{"type": "image", "source": {"type": "url", "url": _URL}}]


def test_claude_varias_imagenes_conservan_orden():
    msgs = [Message(
        role="user",
        content="dos capturas",
        images=[ImageBlock.desde_base64(_B64, "image/png"), ImageBlock.desde_url(_URL)],
    )]
    _system, cuerpo = ClaudeProvider(api_key="x").traducir_mensajes(msgs)
    bloques = cuerpo[0]["content"]
    assert bloques[0]["type"] == "text"
    assert bloques[1]["source"]["type"] == "base64"
    assert bloques[2]["source"] == {"type": "url", "url": _URL}


# ------------------------------ OpenAI: mensaje con imagen ------------------------------

def test_openai_imagen_base64_arma_data_url():
    msgs = [Message(
        role="user",
        content="¿Cuánto es este recibo?",
        images=[ImageBlock.desde_base64(_B64, "image/jpeg")],
    )]
    cuerpo = OpenAIProvider(api_key="x").traducir_mensajes(msgs)
    partes = cuerpo[0]["content"]
    assert partes[0] == {"type": "text", "text": "¿Cuánto es este recibo?"}
    assert partes[1] == {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{_B64}"},
    }


def test_openai_imagen_url_pasa_directo():
    msgs = [Message(role="user", content="", images=[ImageBlock.desde_url(_URL)])]
    cuerpo = OpenAIProvider(api_key="x").traducir_mensajes(msgs)
    partes = cuerpo[0]["content"]
    assert partes == [{"type": "image_url", "image_url": {"url": _URL}}]


# ------------------------------ system/tool intactos con imagen presente ------------------------------

def test_claude_system_y_tool_intactos_con_imagen_en_user():
    msgs = [
        Message(role="system", content="eres un extractor"),
        Message(role="user", content="mira", images=[ImageBlock.desde_url(_URL)]),
    ]
    system, cuerpo = ClaudeProvider(api_key="x").traducir_mensajes(msgs)
    assert system == "eres un extractor"        # el system se separa, como siempre
    assert cuerpo[0]["content"][0] == {"type": "text", "text": "mira"}
    assert cuerpo[0]["content"][1]["type"] == "image"


# ------------------------------ generate() con cliente mockeado (sin API real) ------------------------------

_CLAUDE_RAW = {
    "model": "claude-sonnet",
    "stop_reason": "end_turn",
    "content": [{"type": "text", "text": '{"monto": 50000}'}],
    "usage": {"input_tokens": 20, "output_tokens": 6},
}
_OPENAI_RAW = {
    "model": "gpt-4o",
    "choices": [{"finish_reason": "stop", "message": {"content": '{"monto": 50000}'}}],
    "usage": {"prompt_tokens": 20, "completion_tokens": 6},
}


async def test_claude_generate_pasa_imagen_en_payload_sin_api_real():
    capturado = {}

    async def _fake_client(payload):
        capturado["payload"] = payload
        return _CLAUDE_RAW

    provider = ClaudeProvider(api_key="x", client=_fake_client)
    resp = await provider.generate(
        messages=[Message(
            role="user", content="extrae el recibo",
            images=[ImageBlock.desde_base64(_B64, "image/jpeg")],
        )],
        tools=[], model="claude-sonnet",
    )
    assert resp.text == '{"monto": 50000}'
    bloques = capturado["payload"]["messages"][0]["content"]
    assert bloques[0]["type"] == "text"
    assert bloques[1]["type"] == "image"
    assert bloques[1]["source"]["data"] == _B64


async def test_openai_generate_pasa_imagen_en_payload_sin_api_real():
    capturado = {}

    async def _fake_client(payload):
        capturado["payload"] = payload
        return _OPENAI_RAW

    provider = OpenAIProvider(api_key="x", client=_fake_client)
    resp = await provider.generate(
        messages=[Message(
            role="user", content="extrae el recibo",
            images=[ImageBlock.desde_url(_URL)],
        )],
        tools=[], model="gpt-4o",
    )
    assert resp.text == '{"monto": 50000}'
    partes = capturado["payload"]["messages"][0]["content"]
    assert partes[0]["type"] == "text"
    assert partes[1] == {"type": "image_url", "image_url": {"url": _URL}}
