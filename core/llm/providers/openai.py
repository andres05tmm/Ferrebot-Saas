"""Proveedor OpenAI. Solo traduce canónico ↔ formato OpenAI; no tiene lógica.

El cliente HTTP/SDK se inyecta (pruebas) o se construye perezosamente (producción). Importar
este módulo NO importa el SDK de OpenAI.
"""
import json
from collections.abc import Awaitable, Callable
from typing import Any

from core.llm.base import ImageBlock, LLMError, LLMResponse, Message, ToolCall, ToolSpec

Cliente = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

# Explicit request timeout: the SDK default (10 min) would pin the tenant's DB session
# (pool_size=2) for the whole hang.
_TIMEOUT_S = 60.0

# One SDK client per api_key, reused across generate() calls (connection pooling, no leak).
_sdk_clients: dict[str, Any] = {}


class OpenAIProvider:
    nombre = "openai"

    def __init__(self, *, api_key: str, client: Cliente | None = None) -> None:
        self.api_key = api_key
        self._client = client

    # --- Traducción canónico → OpenAI ---------------------------------------
    def traducir_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters,
            }}
            for t in tools
        ]

    def traducir_mensajes(self, messages: list[Message]) -> list[dict[str, Any]]:
        cuerpo: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                cuerpo.append({
                    "role": "tool", "tool_call_id": m.tool_call_id,
                    "name": m.name, "content": m.content,
                })
            elif m.role == "assistant" and m.tool_calls:
                cuerpo.append({
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {"id": tc.id, "type": "function", "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        }}
                        for tc in m.tool_calls
                    ],
                })
            elif m.images:
                # Visión: texto (si hay) + partes image_url, en el arreglo multimodal de OpenAI.
                cuerpo.append({"role": m.role, "content": self._bloques_con_imagenes(m)})
            else:
                cuerpo.append({"role": m.role, "content": m.content})
        return cuerpo

    @staticmethod
    def _bloques_con_imagenes(m: Message) -> list[dict[str, Any]]:
        """Arma las partes de contenido de un mensaje con imágenes: texto primero, luego cada imagen."""
        partes: list[dict[str, Any]] = []
        if m.content:
            partes.append({"type": "text", "text": m.content})
        partes.extend(OpenAIProvider._bloque_imagen(img) for img in m.images)
        return partes

    @staticmethod
    def _bloque_imagen(img: ImageBlock) -> dict[str, Any]:
        """Traduce un `ImageBlock` canónico a la parte `image_url` de OpenAI (data URL o URL http)."""
        if img.url is not None:
            url = img.url
        else:
            url = f"data:{img.media_type};base64,{img.data}"
        return {"type": "image_url", "image_url": {"url": url}}

    def _payload(
        self, messages: list[Message], tools: list[ToolSpec], model: str,
        system: str | None, extra: dict[str, Any],
    ) -> dict[str, Any]:
        cuerpo = self.traducir_mensajes(messages)
        if system:
            cuerpo = [{"role": "system", "content": system}, *cuerpo]
        payload: dict[str, Any] = {
            "model": model,
            "messages": cuerpo,
            "tools": self.traducir_tools(tools),
        }
        payload.update(extra)
        return payload

    # --- OpenAI → canónico --------------------------------------------------
    def parsear_respuesta(self, raw: dict[str, Any]) -> LLMResponse:
        choices = raw.get("choices", [])
        if not choices:
            return LLMResponse(text=None, model=raw.get("model"), raw=raw)
        choice = choices[0]
        mensaje = choice.get("message", {})
        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=_cargar_args(tc["function"].get("arguments", "{}")),
            )
            for tc in (mensaje.get("tool_calls") or [])
        ]
        return LLMResponse(
            text=mensaje.get("content"),
            tool_calls=tool_calls,
            model=raw.get("model"),
            stop_reason=choice.get("finish_reason"),
            usage=raw.get("usage", {}),
            raw=raw,
        )

    async def generate(
        self, *, messages: list[Message], tools: list[ToolSpec], model: str,
        system: str | None = None, **kwargs: Any,
    ) -> LLMResponse:
        payload = self._payload(messages, tools, model, system, dict(kwargs))
        client = self._client or _cliente_openai(self.api_key)
        return self.parsear_respuesta(await client(payload))


def _cargar_args(arguments: Any) -> dict[str, Any]:
    """OpenAI manda los argumentos como string JSON; los normalizamos a dict."""
    if isinstance(arguments, dict):
        return arguments
    try:
        return json.loads(arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def _cliente_openai(api_key: str) -> Cliente:
    """Real client (lazy): imports the SDK on first call, cached per api_key with a timeout."""
    async def _call(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - depende del entorno
            raise LLMError(
                "SDK 'openai' no instalado; inyecta un `client` (en pruebas) o agrégalo a deps"
            ) from exc
        client = _sdk_clients.get(api_key)
        if client is None:
            client = AsyncOpenAI(api_key=api_key, timeout=_TIMEOUT_S)
            _sdk_clients[api_key] = client
        try:
            resp = await client.chat.completions.create(**payload)
        except Exception as exc:  # traduce el error del SDK a la excepción canónica (retry-able o no)
            from core.llm.resiliencia import clasificar_excepcion
            raise clasificar_excepcion(exc) from exc
        return resp.model_dump()
    return _call
