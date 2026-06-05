"""Entregable 5 — filtros de voz: descartar alucinaciones de Whisper sobre silencio/ruido.

Una alucinación registrada como venta sería una venta fantasma. `es_transcripcion_silencio` debe
devolver True para las despedidas/créditos típicos de Whisper y para `no_speech_prob` alto, y False
para un comando real de mostrador. Función pura (stdlib): sin red, sin BD.
"""
from core.voz.filtros import UMBRAL_NO_SPEECH, es_transcripcion_silencio


def test_descarta_alucinacion_gracias_por_ver_el_video():
    assert es_transcripcion_silencio("¡Gracias por ver el video!", []) is True


def test_descarta_alucinacion_amara_org():
    texto = "Subtítulos realizados por la comunidad de Amara.org"
    assert es_transcripcion_silencio(texto, []) is True


def test_descarta_texto_vacio_o_blanco():
    assert es_transcripcion_silencio("   ", []) is True


def test_descarta_por_no_speech_prob_alto():
    # Texto no alucinatorio, pero los segmentos reportan que NO hubo habla (ruido/silencio).
    segmentos = [{"text": " mmm", "no_speech_prob": 0.92}]
    assert es_transcripcion_silencio("mmm", segmentos) is True


def test_acepta_comando_real_con_no_speech_bajo():
    segmentos = [{"text": "2 martillos", "no_speech_prob": 0.04}]
    assert es_transcripcion_silencio("2 martillos", segmentos) is False


def test_no_speech_bajo_no_descarta_aunque_no_haya_texto_conocido():
    # Justo por debajo del umbral: es habla real, no se descarta.
    segmentos = [{"text": "fiar 5 mil a Juan", "no_speech_prob": UMBRAL_NO_SPEECH - 0.01}]
    assert es_transcripcion_silencio("fiar 5 mil a Juan", segmentos) is False
