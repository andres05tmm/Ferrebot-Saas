"""Puerto de transcripción de voz (Whisper) y su adaptador OpenAI.

`Transcriptor` es el puerto: recibe los bytes del audio y devuelve la `Transcripcion` (texto +
segmentos con metadatos como `no_speech_prob`, que los filtros usan para descartar silencio). El
adaptador real (httpx contra la API de OpenAI) se cablea en el composition root; aquí solo el
puerto + el esqueleto, para que el handler del turno se pruebe con un transcriptor falso (cero red).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Transcripcion:
    """Resultado de transcribir: el texto plano + los segmentos (con `no_speech_prob`, `text`, …)."""

    texto: str
    segmentos: list[dict] = field(default_factory=list)


class Transcriptor(Protocol):
    """Puerto de transcripción. Faked en tests; implementado por `WhisperTranscriptor` en prod."""

    async def transcribir(self, audio: bytes, *, prompt: str | None = None) -> Transcripcion: ...


class WhisperTranscriptor:
    """Adaptador OpenAI Whisper (esqueleto; el cliente httpx real se cablea en el composition root)."""

    def __init__(self, api_key: str, *, model: str = "whisper-1") -> None:
        self._api_key = api_key
        self._model = model

    async def transcribir(self, audio: bytes, *, prompt: str | None = None) -> Transcripcion:
        raise NotImplementedError
