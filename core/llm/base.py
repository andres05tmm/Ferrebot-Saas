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
class ImageBlock:
    """Bloque de imagen para el input de visión de un mensaje de usuario (ADITIVO, ver `Message`).

    Fuente ÚNICA y excluyente (exactamente una):
      - `data` + `media_type`: imagen embebida en base64 crudo (SIN prefijo `data:`); p. ej. la foto
        del recibo Bancolombia que llega por Telegram y se descarga a bytes → base64.
      - `url`: la imagen vive en una URL que el proveedor descarga (p. ej. el `secure_url` de Cloudinary).

    Vocabulario canónico agnóstico de vendor: cada provider la traduce a su formato en su borde
    (Anthropic → bloque `image` con `source`; OpenAI → parte `image_url`). Nada fuera de
    `core/llm/providers/` conoce esas formas.
    """
    media_type: str | None = None   # p. ej. "image/jpeg" | "image/png" (obligatorio con base64)
    data: str | None = None          # base64 crudo, SIN el prefijo `data:...;base64,`
    url: str | None = None

    def __post_init__(self) -> None:
        tiene_base64 = self.data is not None
        tiene_url = self.url is not None
        if tiene_base64 == tiene_url:
            raise ValueError(
                "ImageBlock requiere exactamente UNA fuente: base64 (`data`+`media_type`) o `url`"
            )
        if tiene_base64 and not self.media_type:
            raise ValueError("ImageBlock base64 requiere `media_type` (p. ej. 'image/jpeg')")

    @classmethod
    def desde_base64(cls, data: str, media_type: str) -> "ImageBlock":
        """Imagen embebida en base64 crudo (sin el prefijo `data:`). El camino del bot de Telegram."""
        return cls(media_type=media_type, data=data)

    @classmethod
    def desde_url(cls, url: str, media_type: str | None = None) -> "ImageBlock":
        """Imagen por URL pública (el proveedor la descarga). `media_type` es opcional aquí."""
        return cls(url=url, media_type=media_type)


@dataclass(frozen=True, slots=True)
class Message:
    """Mensaje de la conversación. `role`: system | user | assistant | tool.

    Un mensaje `assistant` que invocó herramientas lleva sus `tool_calls`; el siguiente mensaje
    `tool` (con `tool_call_id`) trae el resultado. Así se arma la tripleta tool_use→tool_result
    que esperan tanto Claude como OpenAI; cada provider la traduce a su formato.

    `images` (visión, ADITIVO): bloques de imagen que acompañan al texto en un mensaje de usuario.
    Vacío por defecto → el mensaje viaja como texto plano (`content` string), sin cambiar la forma ni
    el comportamiento de las llamadas de solo-texto existentes. Con imágenes, el provider arma el
    arreglo de bloques (texto primero, luego imágenes) que exige el input multimodal del vendor.
    """
    role: str
    content: str
    tool_call_id: str | None = None   # respuesta de una herramienta (role=tool)
    name: str | None = None           # nombre de la herramienta que respondió
    tool_calls: list[ToolCall] = field(default_factory=list)  # herramientas que pidió el assistant
    images: list[ImageBlock] = field(default_factory=list)    # visión: imágenes junto al texto (input de usuario)


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
