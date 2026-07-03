"""Puerto de transcripción de voz (Whisper) y su adaptador OpenAI.

`Transcriptor` es el puerto: recibe los bytes del audio y devuelve la `Transcripcion` (texto +
segmentos con metadatos como `no_speech_prob`, que los filtros usan para descartar silencio).

`WhisperTranscriptor` sigue el patrón de `core/llm/providers/openai.py`: el HTTP se aísla tras un
**cliente inyectable** (`Cliente`), con impl real PEREZOSA construida desde la `api_key`. Importar
este módulo NO importa el SDK ni abre red; armar la request y parsear la respuesta son testeables
con un cliente fake (cero red). El adaptador real se cablea en el composition root (CR-3).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# Cliente HTTP de transcripción: payload lógico → respuesta cruda (verbose_json). Mirror del provider.
Cliente = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class TranscripcionError(Exception):
    """Fallo al transcribir (OpenAI respondió error o una respuesta sin los campos esperados)."""


@dataclass(frozen=True, slots=True)
class Transcripcion:
    """Resultado de transcribir: el texto plano + los segmentos (con `no_speech_prob`, `text`, …)."""

    texto: str
    segmentos: list[dict] = field(default_factory=list)


class Transcriptor(Protocol):
    """Puerto de transcripción. Faked en tests; implementado por `WhisperTranscriptor` en prod."""

    async def transcribir(self, audio: bytes, *, prompt: str | None = None) -> Transcripcion: ...


class WhisperTranscriptor:
    """Adaptador OpenAI Whisper. El cliente HTTP se inyecta (tests) o se construye perezoso (prod)."""

    def __init__(
        self, *, api_key: str, model: str = "whisper-1", client: Cliente | None = None
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    def _payload(self, audio: bytes, prompt: str | None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._model,
            "response_format": "verbose_json",
            "language": "es",          # hardcoded por ahora; per-empresa es follow-up
            "file": audio,
        }
        if prompt is not None:
            payload["prompt"] = prompt
        return payload

    async def transcribir(self, audio: bytes, *, prompt: str | None = None) -> Transcripcion:
        client = self._client or _cliente_whisper(self._api_key)
        raw = await client(self._payload(audio, prompt))
        if "error" in raw:
            detalle = (raw.get("error") or {}).get("message") or "transcripción falló"
            raise TranscripcionError(detalle)
        return Transcripcion(texto=raw.get("text", ""), segmentos=raw.get("segments", []))


def _cliente_whisper(api_key: str) -> Cliente:
    """Cliente real (perezoso): importa httpx solo al invocar, no al cargar el módulo.

    Traduce el payload lógico al multipart de OpenAI. El multipart necesita un filename con
    extensión para que OpenAI infiera el formato → se manda como `audio.ogg` (voz de Telegram
    = OGG/Opus); el resto van como campos de formulario.
    """
    async def _call(payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        audio = payload["file"]
        data = {k: v for k, v in payload.items() if k != "file"}
        # El default de httpx (5s) corta transcripciones normales: subir el audio + esperar a
        # Whisper toma más que eso con notas de voz de varios segundos.
        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as cliente:
            resp = await cliente.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data=data,
                files={"file": ("audio.ogg", audio, "audio/ogg")},
            )
        return resp.json()

    return _call
