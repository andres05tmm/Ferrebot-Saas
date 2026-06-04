"""CR-2 — confirmación entre turnos en el TurnoHandler (re-despacho determinista, con fakes).

Pin del flujo:
  (a) turno normal cuyo RespuestaAgente trae `confirmacion_pendiente` → se guarda en el ConfirmStore
      (con la idempotency_key del ctx) y se responde el resumen;
  (b) turno siguiente afirmativo con pendiente → el dispatcher re-ejecuta el tool_call guardado, con
      confirmado=True y la MISMA idempotency_key, SIN llamar a ejecutar_turno; se borra el pendiente;
  (c) turno negativo → borra el pendiente, responde MENSAJE_CANCELADO, no ejecuta nada;
  (d) comando nuevo (ni sí ni no) con pendiente → descarta el pendiente y procesa como turno normal;
  (e) confirm=None → idéntico a hoy (no regresión);
  (f) confirmación por VOZ: nota de voz que transcribe a "sí" con pendiente → re-despacha.
"""
from ai.agent import RespuestaAgente
from ai.confirmacion import Pendiente
from ai.envelope import Contexto, Resultado
from ai.turno import MENSAJE_CANCELADO, crear_turno_handler
from apps.bot.ports import UpdateBot
from core.llm.base import ToolCall
from core.llm.factory import LLMResuelto
from core.voz.transcriptor import Transcripcion

_SESSION = object()
_TOOLCALL = ToolCall(id="call_1", name="registrar_gasto",
                     arguments={"monto": 15000, "categoria": "transporte"})


# --------------------------------- fakes ----------------------------------

class FakeLLM:
    nombre = "fake"
    api_key = "k"

    async def generate(self, **kw):
        raise AssertionError("no debería llamarse")


class FakeDispatcher:
    """Implementa seleccionar_proveedor (turno normal) y ejecutar (re-despacho de confirmación)."""

    def __init__(self, resultado_ejecutar=None):
        self._resultado = resultado_ejecutar
        self.ejecutadas: list[tuple] = []

    async def seleccionar_proveedor(self, empresa_id, *, turno=None):
        return LLMResuelto(provider=FakeLLM(), model="modelo-x", provider_nombre="fake")

    async def ejecutar(self, tool_call, ctx, recursos):
        self.ejecutadas.append((tool_call, ctx, recursos))
        return self._resultado


class FakeConfirmStore:
    def __init__(self):
        self._store: dict[tuple[int, int], Pendiente] = {}
        self.guardados: list[tuple] = []
        self.borrados: list[tuple] = []

    def sembrar(self, tenant_id, chat_id, pendiente: Pendiente):
        self._store[(tenant_id, chat_id)] = pendiente

    async def guardar(self, tenant_id, chat_id, *, tool_call, idempotency_key):
        self.guardados.append((tenant_id, chat_id, tool_call, idempotency_key))
        self._store[(tenant_id, chat_id)] = Pendiente(tool_call=tool_call, idempotency_key=idempotency_key)

    async def obtener(self, tenant_id, chat_id):
        return self._store.get((tenant_id, chat_id))

    async def borrar(self, tenant_id, chat_id):
        self.borrados.append((tenant_id, chat_id))
        self._store.pop((tenant_id, chat_id), None)


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
    def __init__(self, transcripcion):
        self._t = transcripcion

    async def transcribir(self, audio, *, prompt=None):
        return self._t


class FakeArchivos:
    async def descargar(self, file_id):
        return b"OGG"


class FakeEjecutar:
    def __init__(self, respuesta):
        self._r = respuesta
        self.llamadas = []

    async def __call__(self, **kw):
        self.llamadas.append(kw)
        return self._r


# --------------------------------- helpers --------------------------------

def _ctx() -> Contexto:
    return Contexto(tenant_id=1, usuario_id=42, rol="vendedor", origen="bot",
                    idempotency_key="idem-abc", capacidades=frozenset({"bot_telegram", "ventas_voz"}))


def _update(texto="sí", chat_id=555, voz=None) -> UpdateBot:
    return UpdateBot(update_id=100, chat_id=chat_id, telegram_id=555, texto=texto, voz_file_id=voz)


def _resp(texto="ok", *, confirmacion_pendiente=None) -> RespuestaAgente:
    return RespuestaAgente(texto=texto, ruta="texto", confirmacion_pendiente=confirmacion_pendiente)


def _handler(*, confirm, dispatcher=None, ejecutar=None, memoria=None, transcriptor=None, archivos=None):
    return crear_turno_handler(
        dispatcher=dispatcher or FakeDispatcher(),
        memoria=lambda s: memoria or FakeMemoria(),
        costos=lambda s: FakeCostos(),
        crear_recursos=lambda s: object(),
        ejecutar=ejecutar or FakeEjecutar(_resp()),
        confirm=confirm,
        transcriptor=transcriptor,
        archivos=archivos,
    )


def _resultado() -> Resultado:
    return Resultado(data={}, resumen="Gasto de $15.000 registrado.",
                     evento="gasto_registrado", idempotente="aplicada")


# ---------------------------------- tests ---------------------------------

async def test_turno_normal_con_pendiente_guarda_en_confirm_store():
    confirm = FakeConfirmStore()
    ejecutar = FakeEjecutar(_resp("¿Confirmo el gasto?", confirmacion_pendiente=_TOOLCALL))
    notif = FakeNotificador()
    handler = _handler(confirm=confirm, ejecutar=ejecutar)

    await handler(_update(texto="gasto 15 mil en transporte"), _ctx(), _SESSION, notif)

    assert confirm.guardados == [(1, 555, _TOOLCALL, "idem-abc")]   # key del ctx
    assert notif.enviados == [(555, "¿Confirmo el gasto?")]


async def test_afirmacion_redespacha_el_pendiente_sin_modelo():
    confirm = FakeConfirmStore()
    confirm.sembrar(1, 555, Pendiente(tool_call=_TOOLCALL, idempotency_key="idem-prev"))
    dispatcher = FakeDispatcher(resultado_ejecutar=_resultado())
    ejecutar = FakeEjecutar(_resp())
    notif = FakeNotificador()
    handler = _handler(confirm=confirm, dispatcher=dispatcher, ejecutar=ejecutar)

    await handler(_update(texto="sí"), _ctx(), _SESSION, notif)

    assert ejecutar.llamadas == []                       # NO se llama al modelo
    assert len(dispatcher.ejecutadas) == 1
    tool_call, ctx2, _recursos = dispatcher.ejecutadas[0]
    assert tool_call is _TOOLCALL
    assert ctx2.confirmado is True
    assert ctx2.idempotency_key == "idem-prev"           # se reusa la key guardada
    assert notif.enviados == [(555, "Gasto de $15.000 registrado.")]
    assert confirm.borrados == [(1, 555)]                # el pendiente se consume


async def test_negacion_cancela_el_pendiente():
    confirm = FakeConfirmStore()
    confirm.sembrar(1, 555, Pendiente(tool_call=_TOOLCALL, idempotency_key="idem-prev"))
    dispatcher = FakeDispatcher(resultado_ejecutar=_resultado())
    ejecutar = FakeEjecutar(_resp())
    notif = FakeNotificador()
    handler = _handler(confirm=confirm, dispatcher=dispatcher, ejecutar=ejecutar)

    await handler(_update(texto="no"), _ctx(), _SESSION, notif)

    assert notif.enviados == [(555, MENSAJE_CANCELADO)]
    assert dispatcher.ejecutadas == [] and ejecutar.llamadas == []
    assert confirm.borrados == [(1, 555)]


async def test_comando_nuevo_descarta_pendiente_y_sigue_turno_normal():
    confirm = FakeConfirmStore()
    confirm.sembrar(1, 555, Pendiente(tool_call=_TOOLCALL, idempotency_key="idem-prev"))
    dispatcher = FakeDispatcher(resultado_ejecutar=_resultado())
    ejecutar = FakeEjecutar(_resp("3 taladros registrados."))
    notif = FakeNotificador()
    handler = _handler(confirm=confirm, dispatcher=dispatcher, ejecutar=ejecutar)

    await handler(_update(texto="3 taladros"), _ctx(), _SESSION, notif)

    assert confirm.borrados == [(1, 555)]                # se descarta el pendiente
    assert len(ejecutar.llamadas) == 1                   # procesa como turno normal
    assert dispatcher.ejecutadas == []                   # sin re-despacho
    assert notif.enviados == [(555, "3 taladros registrados.")]


async def test_confirm_none_se_comporta_como_hoy():
    ejecutar = FakeEjecutar(_resp("Venta registrada."))
    notif = FakeNotificador()
    handler = _handler(confirm=None, ejecutar=ejecutar)

    await handler(_update(texto="2 martillos"), _ctx(), _SESSION, notif)

    assert len(ejecutar.llamadas) == 1
    assert notif.enviados == [(555, "Venta registrada.")]


async def test_confirmacion_por_voz_redespacha():
    confirm = FakeConfirmStore()
    confirm.sembrar(1, 555, Pendiente(tool_call=_TOOLCALL, idempotency_key="idem-prev"))
    dispatcher = FakeDispatcher(resultado_ejecutar=_resultado())
    ejecutar = FakeEjecutar(_resp())
    notif = FakeNotificador()
    transcriptor = FakeTranscriptor(Transcripcion(texto="sí", segmentos=[{"no_speech_prob": 0.03}]))
    handler = _handler(
        confirm=confirm, dispatcher=dispatcher, ejecutar=ejecutar,
        transcriptor=transcriptor, archivos=FakeArchivos(),
    )

    await handler(_update(texto=None, voz="VOICE123"), _ctx(), _SESSION, notif)

    assert ejecutar.llamadas == []                       # voz "sí" → re-despacho, sin modelo
    assert len(dispatcher.ejecutadas) == 1
    assert dispatcher.ejecutadas[0][0] is _TOOLCALL
    assert notif.enviados == [(555, "Gasto de $15.000 registrado.")]
