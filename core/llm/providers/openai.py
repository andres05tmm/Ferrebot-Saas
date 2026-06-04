"""Proveedor OpenAI. Solo traduce canónico ↔ formato OpenAI; no tiene lógica.

El cliente HTTP/SDK se inyecta (pruebas) o se construye perezosamente (producción). Importar
este módulo NO importa el SDK de OpenAI.
"""
import json
from collections.abc import Awaitable, Callable
from typing import Any

from core.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

Cliente = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


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
            else:
                cuerpo.append({"role": m.role, "content": m.content})
        return cuerpo

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
    """Cliente real (perezoso): importa el SDK solo al invocar, no al cargar el módulo."""
    async def _call(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - depende del entorno
            raise LLMError(
                "SDK 'openai' no instalado; inyecta un `client` (en pruebas) o agrégalo a deps"
            ) from exc
        resp = await AsyncOpenAI(api_key=api_key).chat.completions.create(**payload)
        return resp.model_dump()
    return _call
