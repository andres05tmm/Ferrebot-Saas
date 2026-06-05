"""Entregable 5 — pipeline de voz en el TurnoHandler (con fakes: cero red, cero PG).

Pin del contrato de voz:
  (a) voz → descarga + transcribe y `ejecutar_turno` recibe el texto transcrito;
  (b) transcripción de silencio/alucinación → responde "no entendí" y NO llama a `ejecutar_turno`
      (evita ventas fantasma);
  (c) sin capacidad `ventas_voz` → mensaje amable; no descarga, no transcribe, no ejecuta;
  (d) fallo de descarga o de transcripción → MENSAJE_RESPALDO, sin propagar;
  (e) audio_logs best-effort: se registra la transcripción y, si el repo falla, el turno responde;
  (f) el texto transcrito se persiste como mensaje del usuario (no "").
"""
from ai.agent import RespuestaAgente
from ai.envelope import Contexto
from ai.turno import (
    MENSAJE_NO_ENTENDI,
    MENSAJE_RESPALDO,
    MENSAJE_VOZ_DESHABILITADA,
    crear_turno_handler,
)
from apps.bot.ports import UpdateBot
from core.llm.factory import LLMResuelto
from core.voz.transcriptor import Transcripcion

_SESSION = object()
_COMANDO = "2 martillos"
_ALUCINACION = "¡Gracias por ver el video!"


# --------------------------------- fakes ----------------------------------

class FakeLLM:
    nombre = "fake"
    api_key = "k"

    async def generate(self, **kw):
        raise AssertionError("no debería llamarse")


class FakeDispatcher:
    async def seleccionar_proveedor(self, empresa_id, *, turno=None):
        return LLMResuelto(provider=FakeLLM(), model="modelo-x", provider_nombre="fake")


class FakeMemoria:
    def __init__(self):
        self.guardados = []

    async def cargar_historial(self, chat_id, *, limite=8):
        return []

    async def leer_entidades(self, chat_id):
        return {}

    async def guardar_turno(self, chat_id, *, usuario, asistente):
        self.guardados.append((chat_id, usuario, asistente))


class FakeCostos:
    async def acumular(self, *, fecha, modelo, tokens_in, tokens_out):
        pass


class FakeNotificador:
    def __init__(self):
        self.enviados = []

    async def responder(self, chat_id, texto):
        self.enviados.append((chat_id, texto))


class FakeTranscriptor:
    def __init__(self, transcripcion=None, *, error=None):
        self._t = transcripcion
        self._error = error
        self.llamadas = []

    async def transcribir(self, audio, *, prompt=None):
        self.llamadas.append({"audio": audio, "prompt": prompt})
        if self._error is not None:
            raise self._error
        return self._t


class FakeArchivos:
    def __init__(self, audio=b"OGG-bytes", *, error=None):
        self._audio = audio
        self._error = error
        self.descargas = []

    async def descargar(self, file_id):
        self.descargas.append(file_id)
        if self._error is not None:
            raise self._error
        return self._audio


class FakeBundle:
    def __init__(self, transcriptor, archivos):
        self.notificador = None        # la voz usa transcriptor/archivos; el notificador llega por turno
        self.transcriptor = transcriptor
        self.archivos = archivos


class FakeRecursosBot:
    """`RecursosBot` falso: el handler pide `para(ctx.tenant_id)` y usa el transcriptor/archivos de
    ESA empresa (multi-empresa: una sola app, un api-key/bot-token por empresa)."""

    def __init__(self, transcriptor, archivos):
        self._bundle = FakeBundle(transcriptor, archivos)
        self.empresas = []

    async def para(self, empresa_id):
        self.empresas.append(empresa_id)
        return self._bundle


class FakeAudioLogs:
    def __init__(self, *, falla=False):
        self.registros = []
        self.falla = falla

    async def registrar(self, chat_id, transcripcion, duracion):
        if self.falla:
            raise RuntimeError("fallo al registrar audio_log")
        self.registros.append((chat_id, transcripcion, duracion))


class FakeEjecutar:
    def __init__(self, respuesta):
        self._r = respuesta
        self.llamadas = []

    async def __call__(self, **kw):
        self.llamadas.append(kw)
        return self._r


# --------------------------------- helpers --------------------------------

def _ctx(*, voz=True) -> Contexto:
    caps = {"bot_telegram"} | ({"ventas_voz"} if voz else set())
    return Contexto(tenant_id=1, usuario_id=42, rol="vendedor", origen="bot",
                    capacidades=frozenset(caps))


def _update_voz(file_id="VOICE123", chat_id=555) -> UpdateBot:
    return UpdateBot(update_id=100, chat_id=chat_id, telegram_id=555, texto=None, voz_file_id=file_id)


def _resp(texto="Listo, registrada.") -> RespuestaAgente:
    return RespuestaAgente(texto=texto, ruta="texto")


def _transcripcion(texto=_COMANDO, no_speech=0.04) -> Transcripcion:
    return Transcripcion(texto=texto, segmentos=[{"text": texto, "no_speech_prob": no_speech}])


def _handler(*, transcriptor, archivos, audios=None, memoria=None, ejecutar=None):
    memoria = memoria or FakeMemoria()
    ejecutar = ejecutar or FakeEjecutar(_resp())
    return crear_turno_handler(
        dispatcher=FakeDispatcher(),
        memoria=lambda s: memoria,
        costos=lambda s: FakeCostos(),
        crear_recursos=lambda s: object(),
        ejecutar=ejecutar,
        recursos=FakeRecursosBot(transcriptor, archivos),   # voz por empresa (CR-3a)
        audios=(lambda s: audios) if audios is not None else None,
    )


# ---------------------------------- tests ---------------------------------

async def test_voz_descarga_transcribe_y_pasa_texto_a_ejecutar():
    archivos = FakeArchivos()
    transcriptor = FakeTranscriptor(_transcripcion(_COMANDO))
    ejecutar = FakeEjecutar(_resp())
    handler = _handler(transcriptor=transcriptor, archivos=archivos, ejecutar=ejecutar)

    await handler(_update_voz(), _ctx(), _SESSION, FakeNotificador())

    assert archivos.descargas == ["VOICE123"]
    assert transcriptor.llamadas and transcriptor.llamadas[0]["audio"] == b"OGG-bytes"
    assert len(ejecutar.llamadas) == 1
    assert ejecutar.llamadas[0]["texto"] == _COMANDO        # el texto transcrito entra al pipeline


async def test_voz_silencio_no_ejecuta_y_responde_no_entendi():
    transcriptor = FakeTranscriptor(_transcripcion(_ALUCINACION, no_speech=0.9))
    ejecutar = FakeEjecutar(_resp())
    notif = FakeNotificador()
    handler = _handler(transcriptor=transcriptor, archivos=FakeArchivos(), ejecutar=ejecutar)

    await handler(_update_voz(), _ctx(), _SESSION, notif)

    assert ejecutar.llamadas == []                          # no se ejecuta el turno (sin venta fantasma)
    assert notif.enviados == [(555, MENSAJE_NO_ENTENDI)]


async def test_voz_sin_capacidad_no_transcribe_ni_ejecuta():
    archivos = FakeArchivos()
    transcriptor = FakeTranscriptor(_transcripcion())
    ejecutar = FakeEjecutar(_resp())
    notif = FakeNotificador()
    handler = _handler(transcriptor=transcriptor, archivos=archivos, ejecutar=ejecutar)

    await handler(_update_voz(), _ctx(voz=False), _SESSION, notif)

    assert notif.enviados == [(555, MENSAJE_VOZ_DESHABILITADA)]
    assert archivos.descargas == [] and transcriptor.llamadas == []
    assert ejecutar.llamadas == []


async def test_voz_fallo_de_descarga_responde_respaldo():
    archivos = FakeArchivos(error=RuntimeError("getFile 500"))
    ejecutar = FakeEjecutar(_resp())
    notif = FakeNotificador()
    handler = _handler(transcriptor=FakeTranscriptor(_transcripcion()), archivos=archivos, ejecutar=ejecutar)

    await handler(_update_voz(), _ctx(), _SESSION, notif)    # no debe propagar

    assert notif.enviados == [(555, MENSAJE_RESPALDO)]
    assert ejecutar.llamadas == []


async def test_voz_fallo_de_transcripcion_responde_respaldo():
    transcriptor = FakeTranscriptor(error=TimeoutError("whisper timeout"))
    ejecutar = FakeEjecutar(_resp())
    notif = FakeNotificador()
    handler = _handler(transcriptor=transcriptor, archivos=FakeArchivos(), ejecutar=ejecutar)

    await handler(_update_voz(), _ctx(), _SESSION, notif)    # no debe propagar

    assert notif.enviados == [(555, MENSAJE_RESPALDO)]
    assert ejecutar.llamadas == []


async def test_voz_registra_audio_log_best_effort():
    audios = FakeAudioLogs()
    handler = _handler(transcriptor=FakeTranscriptor(_transcripcion(_COMANDO)),
                       archivos=FakeArchivos(), audios=audios)

    await handler(_update_voz(), _ctx(), _SESSION, FakeNotificador())

    assert audios.registros == [(555, _COMANDO, None)]      # se bitácora la transcripción


async def test_voz_audio_log_que_falla_no_rompe_el_turno():
    audios = FakeAudioLogs(falla=True)
    notif = FakeNotificador()
    handler = _handler(transcriptor=FakeTranscriptor(_transcripcion(_COMANDO)),
                       archivos=FakeArchivos(), audios=audios)

    await handler(_update_voz(), _ctx(), _SESSION, notif)    # no debe propagar

    assert notif.enviados == [(555, "Listo, registrada.")]  # el turno respondió pese al fallo


async def test_voz_persiste_la_transcripcion_como_mensaje_del_usuario():
    memoria = FakeMemoria()
    handler = _handler(transcriptor=FakeTranscriptor(_transcripcion(_COMANDO)),
                       archivos=FakeArchivos(), memoria=memoria)

    await handler(_update_voz(), _ctx(), _SESSION, FakeNotificador())

    assert memoria.guardados == [(555, _COMANDO, "Listo, registrada.")]   # usuario = transcripción, no ""
