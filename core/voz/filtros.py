"""Filtros de ruido/alucinación de Whisper (porta las REGLAS de `ai/voz_filtros.py` del original).

Whisper "alucina" frases sobre silencio o ruido (típicamente despedidas de YouTube tipo
"gracias por ver el video" o créditos "subtítulos por la comunidad de Amara.org"). Registrar una
venta a partir de eso sería una venta fantasma. `es_transcripcion_silencio` descarta el turno cuando:
  - el texto normalizado coincide con una alucinación conocida (o queda vacío), o
  - los segmentos reportan `no_speech_prob` alto (ruido/silencio, no habla).

Función pura (stdlib). `limpiar_texto_voz` NO se porta aquí: solo aplica con TTS (diferido).
"""
from __future__ import annotations

import unicodedata

# Umbral por defecto de probabilidad de "no habla" por segmento (Whisper).
UMBRAL_NO_SPEECH = 0.6

# Alucinaciones conocidas de Whisper sobre silencio/ruido (normalizadas: sin tildes, minúsculas).
# Portado de bot-ventas-ferreteria/ai/voz_filtros.py: despedidas de YouTube y créditos de subtítulos.
_HALUCINACIONES = frozenset({
    "gracias por ver el video",
    "amara org",
    "subtitulos realizados por la comunidad de amara org",
    "subtitulado por la comunidad de amara org",
    "musica",
    "suscribete",
    "suscribete al canal",
    "no olvides suscribirte",
})


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes ni puntuación, espacios colapsados (para comparar contra el set)."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    limpio = "".join(c if c.isalnum() or c.isspace() else " " for c in sin_tildes.lower())
    return " ".join(limpio.split())


def es_transcripcion_silencio(
    texto: str, segmentos: list[dict], umbral_no_speech: float = UMBRAL_NO_SPEECH
) -> bool:
    """True si la transcripción es silencio/ruido/alucinación (no un comando real del usuario)."""
    normalizado = _normalizar(texto)
    if not normalizado or normalizado in _HALUCINACIONES:
        return True
    return any(
        (seg.get("no_speech_prob") or 0.0) >= umbral_no_speech for seg in segmentos
    )
