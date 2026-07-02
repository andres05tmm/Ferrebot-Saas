"""Tipos canónicos y Protocol de proveedor LLM (agnóstico de vendor).

Todo el sistema habla este vocabulario; cada provider traduce desde/hacia el formato de su
vendor en sus bordes. Nada fuera de `core/llm/providers/` debe conocer la forma de OpenAI/Claude.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class LLMError(Exception):
    """Base de errores de la capa LLM."""


class LLMTransitorio(LLMError):
    """Fallo transitorio del proveedor (429, 5xx, timeout, conexión): reintentable con backoff."""


class LLMPermanente(LLMError):
    """Fallo permanente (4xx de petición, auth): reintentar no lo arregla; no se reintenta."""


class ProveedorDesconocido(LLMError):
    """El nombre de proveedor no está en el registry."""

    def __init__(self, nombre: str) -> None:
        super().__init__(f"Proveedor LLM desconocido: {nombre!r}")
        self.nombre = nombre


class LLMSinCredencial(LLMError):
    """No hay API key para el proveedor (ni por empresa ni de plataforma). Nunca hardcodear."""

    def __init__(self, empresa_id: int, proveedor: str) -> None:
        super().__init__(
            f"Sin API key para proveedor {proveedor!r} (empresa {empresa_id}): "
            "configúrela en secretos_empresa o en el .env de plataforma"
        )
        self.empresa_id = empresa_id
        self.proveedor = proveedor


@dataclass(frozen=True, slots=True)
class Message:
    """Mensaje de la conversación. `role`: system | user | assistant | tool.

    Un mensaje `assistant` que invocó herramientas lleva sus `tool_calls`; el siguiente mensaje
    `tool` (con `tool_call_id`) trae el resultado. Así se arma la tripleta tool_use→tool_result
    que esperan tanto Claude como OpenAI; cada provider la traduce a su formato.
    """
    role: str
    content: str
    tool_call_id: str | None = None   # respuesta de una herramienta (role=tool)
    name: str | None = None           # nombre de la herramienta que respondió
    tool_calls: list[ToolCall] = field(default_factory=list)  # herramientas que pidió el assistant


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Declaración canónica de una herramienta: nombre, descripción y JSON Schema de argumentos."""
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Invocación de herramienta que pidió el modelo, ya normalizada (arguments = dict)."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Respuesta normalizada de cualquier proveedor."""
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str | None = None
    stop_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    raw: Any = None


@runtime_checkable
class LLMProvider(Protocol):
    """Puerto de proveedor. Las implementaciones traducen al vendor y normalizan de vuelta."""

    nombre: str
    api_key: str

    async def generate(
        self,
        *,
        messages: list[Message],
        tools: list[ToolSpec],
        model: str,
        system: str | None = ...,
        **kwargs: Any,
    ) -> LLMResponse: ...
