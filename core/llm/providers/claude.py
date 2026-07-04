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

# Prompt caching (ADR 0024): marca de cache efímera (~5 min) para el PREFIJO estable del prompt
# (tools del catálogo + system por tenant). Anthropic cachea el prefijo hasta el bloque marcado, así
# que basta ponerla en la ÚLTIMA tool y en el bloque system: −costo/−latencia sin romper el prefijo.
_EFIMERO: dict[str, Any] = {"type": "ephemeral"}

# Explicit request timeout: the SDK default (10 min) would pin the tenant's DB session
# (pool_size=2) for the whole hang.
_TIMEOUT_S = 60.0

# One SDK client per api_key, reused across generate() calls (connection pooling, no leak).
_sdk_clients: dict[str, Any] = {}


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
            elif m.role == "assistant" and m.tool_calls:
                bloques: list[dict[str, Any]] = []
                if m.content:
                    bloques.append({"type": "text", "text": m.content})
                bloques.extend(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    for tc in m.tool_calls
                )
                cuerpo.append({"role": "assistant", "content": bloques})
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
            "tools": self._tools_cacheables(tools),
        }
        system_final = system or system_msgs
        if system_final:
            payload["system"] = self._system_cacheable(system_final)
        payload.update(extra)
        return payload

    @staticmethod
    def _tools_cacheables(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        """Traduce el catálogo y pone un breakpoint de caché en la ÚLTIMA tool (prefijo estable).

        `traducir_tools` se mantiene puro (otros consumidores no cargan la marca); el breakpoint
        se agrega solo aquí, sobre la copia del payload. Sin tools no hay nada que cachear.
        """
        traducidas: list[dict[str, Any]] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]
        if traducidas:
            traducidas[-1]["cache_control"] = _EFIMERO
        return traducidas

    @staticmethod
    def _system_cacheable(system: str) -> list[dict[str, Any]]:
        """System como bloque de texto con marca de caché efímera (el system es estable por tenant)."""
        return [{"type": "text", "text": system, "cache_control": _EFIMERO}]

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
    """Real client (lazy): imports the SDK on first call, cached per api_key with a timeout."""
    async def _call(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            from anthropic import AsyncAnthropic
        except ModuleNotFoundError as exc:  # pragma: no cover - depende del entorno
            raise LLMError(
                "SDK 'anthropic' no instalado; inyecta un `client` (en pruebas) o agrégalo a deps"
            ) from exc
        client = _sdk_clients.get(api_key)
        if client is None:
            client = AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT_S)
            _sdk_clients[api_key] = client
        try:
            resp = await client.messages.create(**payload)
        except Exception as exc:  # traduce el error del SDK a la excepción canónica (retry-able o no)
            from core.llm.resiliencia import clasificar_excepcion
            raise clasificar_excepcion(exc) from exc
        return resp.model_dump()
    return _call
