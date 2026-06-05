"""CR-1 — WhisperTranscriptor (con cliente FAKE: cero red, cero SDK).

Pin del contrato:
  - `transcribir` arma la request a OpenAI audio/transcriptions (model=whisper-1,
    response_format=verbose_json, language=es, el audio, y prompt cuando se pasa);
  - parsea el verbose_json → `Transcripcion(texto, segmentos)` conservando `no_speech_prob`
    por segmento (lo necesita el filtro de silencio de E5);
  - un error de OpenAI → lanza `TranscripcionError`.
"""
import pytest

from core.voz.transcriptor import Transcripcion, TranscripcionError, WhisperTranscriptor

# Muestra de respuesta verbose_json de Whisper (recortada a lo que el adaptador usa).
_VERBOSE_JSON = {
    "task": "transcribe",
    "language": "spanish",
    "text": "2 martillos para Juan",
    "segments": [
        {"id": 0, "text": " 2 martillos", "no_speech_prob": 0.05, "avg_logprob": -0.27},
        {"id": 1, "text": " para Juan", "no_speech_prob": 0.08, "avg_logprob": -0.31},
    ],
}


class FakeCliente:
    """Cliente de transcripción falso: captura el payload y devuelve una respuesta pre-cargada."""

    def __init__(self, raw: dict) -> None:
        self._raw = raw
        self.payloads: list[dict] = []

    async def __call__(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return self._raw


async def test_transcribir_arma_request_y_parsea_verbose_json():
    fake = FakeCliente(_VERBOSE_JSON)
    t = await WhisperTranscriptor(api_key="x", client=fake).transcribir(b"OGG-OPUS-BYTES")

    payload = fake.payloads[0]
    assert payload["model"] == "whisper-1"
    assert payload["response_format"] == "verbose_json"
    assert payload["language"] == "es"
    assert payload["file"] == b"OGG-OPUS-BYTES"
    assert "prompt" not in payload                      # sin prompt → no se manda la clave

    assert isinstance(t, Transcripcion)
    assert t.texto == "2 martillos para Juan"
    assert len(t.segmentos) == 2
    assert t.segmentos[0]["no_speech_prob"] == 0.05     # el filtro de E5 depende de esto


async def test_transcribir_incluye_prompt_cuando_se_pasa():
    fake = FakeCliente(_VERBOSE_JSON)
    await WhisperTranscriptor(api_key="x", client=fake).transcribir(
        b"audio", prompt="martillo, taladro, cemento"
    )
    assert fake.payloads[0]["prompt"] == "martillo, taladro, cemento"


async def test_transcribir_lanza_si_openai_responde_error():
    fake = FakeCliente({"error": {"message": "invalid file format"}})
    with pytest.raises(TranscripcionError):
        await WhisperTranscriptor(api_key="x", client=fake).transcribir(b"audio")
