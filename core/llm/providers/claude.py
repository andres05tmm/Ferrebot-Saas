"""Proveedor Claude (Anthropic). Solo traduce canónico ↔ formato Anthropic; no tiene lógica.

El cliente HTTP/SDK se inyecta (para pruebas) o se construye perezosamente (producción). Importar
este módulo NO importa el SDK de Anthropic.
"""
from collections.abc import Awaitable, Callable
from typing import Any

from core.llm.base import (
    LLMError,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
)

Cliente = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class ClaudeProvider:
    nombre = "claude"

    def __init__(self, *, api_key: str, client: Cliente | None = None) -> None:
        self.api_key = api_key
        self._client = client

    # --- Traducción canónico → Anthropic ------------------------------------
    def traducir_tools(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]

    def traducir_mensajes(self, messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
        """Anthropic separa el system del resto; los mensajes tool van como user/tool_result."""
        system_partes = [m.content for m in messages if m.role == "system"]
        cuerpo: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                cuerpo.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content,
                    }],
                })
            else:
                cuerpo.append({"role": m.role, "content": m.content})
        system = "\n".join(system_partes) if system_partes else None
        return system, cuerpo

    def _payload(
        self, messages: list[Message], tools: list[ToolSpec], model: str,
        system: str | None, extra: dict[str, Any],
    ) -> dict[str, Any]:
        system_msgs, cuerpo = self.traducir_mensajes(messages)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": extra.pop("max_tokens", 1024),
            "messages": cuerpo,
            "tools": self.traducir_tools(tools),
        }
        system_final = system or system_msgs
        if system_final:
            payload["system"] = system_final
        payload.update(extra)
        return payload

    # --- Anthropic → canónico -----------------------------------------------
    def parsear_respuesta(self, raw: dict[str, Any]) -> LLMResponse:
        textos: list[str] = []
        tool_calls: list[ToolCall] = []
        for bloque in raw.get("content", []):
            if bloque.get("type") == "text":
                textos.append(bloque.get("text", ""))
            elif bloque.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    id=bloque["id"], name=bloque["name"], arguments=bloque.get("input", {})
                ))
        return LLMResponse(
            text="".join(textos) or None,
            tool_calls=tool_calls,
            model=raw.get("model"),
            stop_reason=raw.get("stop_reason"),
            usage=raw.get("usage", {}),
            raw=raw,
        )

    async def generate(
        self, *, messages: list[Message], tools: list[ToolSpec], model: str,
        system: str | None = None, **kwargs: Any,
    ) -> LLMResponse:
        payload = self._payload(messages, tools, model, system, dict(kwargs))
        client = self._client or _cliente_anthropic(self.api_key)
        return self.parsear_respuesta(await client(payload))


def _cliente_anthropic(api_key: str) -> Cliente:
    """Cliente real (perezoso): importa el SDK solo al invocar, no al cargar el módulo."""
    async def _call(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from anthropic import AsyncAnthropic
        except ModuleNotFoundError as exc:  # pragma: no cover - depende del entorno
            raise LLMError(
                "SDK 'anthropic' no instalado; inyecta un `client` (en pruebas) o agrégalo a deps"
            ) from exc
        resp = await AsyncAnthropic(api_key=api_key).messages.create(**payload)
        return resp.model_dump()
    return _call
