"""Capa de botones del bot (inline keyboards + callbacks) — fase RED.

Conductuales (FALLAN en RED, por NotImplementedError de los esqueletos):
  - `parsear_update` reconoce un `callback_query` → `CallbackBot`;
  - `manejar_update` con un callback válido (secret ok, usuario autorizado) invoca `procesar_callback`;
  - el relay del bypass, ante una venta, ofrece método de pago con botones y NO registra todavía;
  - el callback `pago:efectivo` ejecuta el pendiente (dispatcher) y responde.

Estructurales (PASAN ya en RED, infraestructura):
  - el `Notificador` (TelegramNotificador) acepta `teclado` y expone `answer_callback`;
  - `MetodoPago` incluye 'datafono'.

Todo con fakes (cero red, cero PG), al estilo de los demás tests del bot.
"""
from typing import get_args

import pytest

from ai.bypass import VentaPreparada
from ai.confirmacion import Pendiente
from ai.envelope import Contexto, Resultado
from ai.turno import (
    CALLBACK_CANCELAR,
    MENSAJE_VENTA_CANCELADA,
    MENSAJE_VENTA_EXPIRADA,
    PREFIJO_PAGO,
    crear_callback_handler,
    crear_turno_handler,
)
from apps.bot.ports import Accion, CallbackBot
from apps.bot.telegram import TelegramNotificador
from apps.bot.webhook import manejar_update, parsear_update
from core.llm.base import ToolCall
from modules.ventas.schemas import MetodoPago
from tests.test_bot_webhook import SECRET, make_deps

_SESSION = object()


# ============================ F-A: parseo / enrutado ============================

def _payload_callback(update_id=200, callback_id="cb-1", chat_id=555, telegram_id=555, data="pago:efectivo"):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": callback_id,
            "from": {"id": telegram_id},
            "message": {"message_id": 9, "chat": {"id": chat_id}},
            "data": data,
        },
    }


class SpyProcesarCallback:
    def __init__(self):
        self.llamadas: list[tuple] = []

    async def __call__(self, callback, ctx, session, notificador):
        self.llamadas.append((callback, ctx, session, notificador))


def test_parsear_update_reconoce_callback_query():
    cb = parsear_update(_payload_callback(callback_id="cb-9", data="venta:cancelar"))
    assert isinstance(cb, CallbackBot)
    assert cb.callback_id == "cb-9"
    assert cb.chat_id == 555 and cb.telegram_id == 555
    assert cb.data == "venta:cancelar"


async def test_manejar_update_callback_invoca_procesar_callback():
    spy = SpyProcesarCallback()
    deps = make_deps(procesar_callback=spy)

    res = await manejar_update("puntorojo", SECRET, _payload_callback(data="pago:efectivo"), deps)

    assert res.accion is Accion.PROCESADO
    assert len(spy.llamadas) == 1
    callback, ctx, _session, _notif = spy.llamadas[0]
    assert isinstance(callback, CallbackBot)
    assert callback.data == "pago:efectivo"
    assert ctx.origen == "bot" and ctx.usuario_id == 42      # mismas validaciones que un mensaje


# ============================ F-B: relay con botones ============================

class _FakeMemoria:
    async def cargar_historial(self, chat_id, *, limite=8):
        return []

    async def leer_entidades(self, chat_id):
        return {}

    async def guardar_turno(self, chat_id, *, usuario, asistente):
        pass


class _FakeCostos:
    async def acumular(self, **kw):
        pass


class _SpyDispatcher:
    """Cuenta selecciones de proveedor y ejecuciones de ToolCall."""

    def __init__(self, resultado=None):
        self.selecciones = 0
        self.ejecutados: list[ToolCall] = []
        self._resultado = resultado or Resultado(data={}, resumen="Venta #1 registrada.")

    async def seleccionar_proveedor(self, empresa_id, *, turno=None):
        self.selecciones += 1
        raise AssertionError("el modelo no debería invocarse cuando el bypass resuelve")

    async def ejecutar(self, tool_call, ctx, recursos):
        self.ejecutados.append(tool_call)
        return self._resultado


class _BotonNotificador:
    """Notificador que captura (chat_id, texto, teclado) y los answer_callback."""

    def __init__(self):
        self.mensajes: list[tuple] = []
        self.callbacks_respondidos: list[str] = []

    async def responder(self, chat_id, texto, *, teclado=None):
        self.mensajes.append((chat_id, texto, teclado))

    async def answer_callback(self, callback_id, *, texto=None):
        self.callbacks_respondidos.append(callback_id)


class _FakeBypassPreparar:
    """Bypass falso: `preparar` devuelve una venta lista (sin ejecutar); `intentar` no se usa aquí."""

    def __init__(self, preparada):
        self._preparada = preparada

    async def preparar(self, texto, ctx, recursos):
        return self._preparada

    async def intentar(self, texto, ctx, recursos):  # pragma: no cover - no es la ruta bajo prueba
        raise AssertionError("el flujo con botones usa preparar, no intentar")


class _FakeVentaPendientes:
    def __init__(self):
        self.guardados: list[tuple] = []
        self.borrados: list[tuple] = []
        self._pendiente = None

    async def guardar(self, tenant_id, chat_id, *, tool_call, idempotency_key):
        self._pendiente = Pendiente(tool_call=tool_call, idempotency_key=idempotency_key)
        self.guardados.append((tenant_id, chat_id, tool_call, idempotency_key))

    async def obtener(self, tenant_id, chat_id):
        return self._pendiente

    async def borrar(self, tenant_id, chat_id):
        self.borrados.append((tenant_id, chat_id))
        self._pendiente = None


def _ctx():
    return Contexto(tenant_id=1, usuario_id=42, rol="vendedor", origen="bot",
                    idempotency_key="key-1", capacidades=frozenset({"bot_telegram"}))


def _update(texto="3 vinilo", chat_id=555):
    from apps.bot.ports import UpdateBot
    return UpdateBot(update_id=100, chat_id=chat_id, telegram_id=555, texto=texto)


def _tool_call_sin_metodo():
    return ToolCall(
        id="bypass:7", name="registrar_venta",
        arguments={"items": [{"producto_id": 7, "cantidad": 3}]},   # SIN metodo_pago
    )


async def test_relay_bypass_ofrece_metodo_pago_y_no_registra():
    # El relay (con store de pendientes) debe RESUMIR + mostrar botonera y NO ejecutar la venta.
    preparada = VentaPreparada(tool_call=_tool_call_sin_metodo(), resumen="3 vinilo = $60.000")
    disp = _SpyDispatcher()
    pendientes = _FakeVentaPendientes()
    notif = _BotonNotificador()
    handler = crear_turno_handler(
        dispatcher=disp,
        memoria=lambda s: _FakeMemoria(),
        costos=lambda s: _FakeCostos(),
        crear_recursos=lambda s: object(),
        crear_bypass=lambda s: _FakeBypassPreparar(preparada),
        pendientes=pendientes,
    )

    await handler(_update("3 vinilo"), _ctx(), _SESSION, notif)

    # No se registró la venta todavía (no hubo dispatcher.ejecutar).
    assert disp.ejecutados == []
    # Se guardó el pendiente (la venta queda lista salvo el método de pago).
    assert len(pendientes.guardados) == 1
    # Se mandó el resumen con una botonera: fila [Efectivo, Transferencia, Datafono] + fila [Cancelar].
    assert len(notif.mensajes) == 1
    _chat, _texto, teclado = notif.mensajes[0]
    datas = [data for fila in teclado for _t, data in fila]
    assert f"{PREFIJO_PAGO}efectivo" in datas
    assert f"{PREFIJO_PAGO}transferencia" in datas
    assert f"{PREFIJO_PAGO}datafono" in datas
    assert CALLBACK_CANCELAR in datas


# ============================ F-B: handler de callbacks ========================

async def test_callback_pago_efectivo_ejecuta_pendiente_y_responde():
    pendientes = _FakeVentaPendientes()
    await pendientes.guardar(1, 555, tool_call=_tool_call_sin_metodo(), idempotency_key="key-1")
    disp = _SpyDispatcher(Resultado(data={}, resumen="Venta #1 por $60.000 (efectivo)."))
    notif = _BotonNotificador()
    handler = crear_callback_handler(
        dispatcher=disp,
        pendientes=pendientes,
        crear_recursos=lambda s: object(),
        memoria=lambda s: _FakeMemoria(),
    )

    callback = CallbackBot(callback_id="cb-1", chat_id=555, telegram_id=555,
                           data=f"{PREFIJO_PAGO}efectivo")
    await handler(callback, _ctx(), _SESSION, notif)

    # Ejecutó el pendiente UNA vez, con el método elegido.
    assert len(disp.ejecutados) == 1
    assert disp.ejecutados[0].arguments.get("metodo_pago") == "efectivo"
    # Respondió la confirmación y limpió el pendiente.
    assert any("Venta #1" in texto for _c, texto, _k in notif.mensajes)
    assert pendientes.borrados == [(1, 555)]
    assert notif.callbacks_respondidos == ["cb-1"]      # ack a Telegram en todos los casos


async def test_callback_venta_cancelar_limpia_y_responde():
    pendientes = _FakeVentaPendientes()
    await pendientes.guardar(1, 555, tool_call=_tool_call_sin_metodo(), idempotency_key="key-1")
    disp = _SpyDispatcher()
    notif = _BotonNotificador()
    handler = crear_callback_handler(
        dispatcher=disp, pendientes=pendientes,
        crear_recursos=lambda s: object(), memoria=lambda s: _FakeMemoria(),
    )

    callback = CallbackBot(callback_id="cb-2", chat_id=555, telegram_id=555, data=CALLBACK_CANCELAR)
    await handler(callback, _ctx(), _SESSION, notif)

    assert disp.ejecutados == []                         # no registró nada
    assert pendientes.borrados == [(1, 555)]             # limpió el pendiente
    assert any(MENSAJE_VENTA_CANCELADA in texto for _c, texto, _k in notif.mensajes)
    assert notif.callbacks_respondidos == ["cb-2"]


async def test_doble_tap_pago_no_duplica():
    pendientes = _FakeVentaPendientes()
    await pendientes.guardar(1, 555, tool_call=_tool_call_sin_metodo(), idempotency_key="key-1")
    disp = _SpyDispatcher(Resultado(data={}, resumen="Venta #1 registrada."))
    notif = _BotonNotificador()
    handler = crear_callback_handler(
        dispatcher=disp, pendientes=pendientes,
        crear_recursos=lambda s: object(), memoria=lambda s: _FakeMemoria(),
    )

    callback = CallbackBot(callback_id="cb-3", chat_id=555, telegram_id=555,
                           data=f"{PREFIJO_PAGO}efectivo")
    await handler(callback, _ctx(), _SESSION, notif)     # 1er tap: registra
    await handler(callback, _ctx(), _SESSION, notif)     # 2º tap (doble-tap)

    assert len(disp.ejecutados) == 1                     # una sola venta
    # el 2º tap ya no encuentra el pendiente → avisa que expiró (sin re-ejecutar)
    assert any(MENSAJE_VENTA_EXPIRADA in texto for _c, texto, _k in notif.mensajes)
    assert notif.callbacks_respondidos == ["cb-3", "cb-3"]   # ack en ambos taps


# ============================ estructurales (PASAN) ============================

class _FakeCliente:
    def __init__(self, respuestas):
        self._respuestas = list(respuestas)
        self.llamadas: list[tuple] = []

    async def call(self, metodo, payload):
        self.llamadas.append((metodo, payload))
        return self._respuestas.pop(0)

    async def download(self, url):  # pragma: no cover - no usado aquí
        return b""


async def test_notificador_responder_acepta_teclado():
    fake = _FakeCliente([{"ok": True, "result": {"message_id": 1}}])
    teclado = [[("Efectivo", "pago:efectivo")], [("Cancelar", "venta:cancelar")]]
    await TelegramNotificador(bot_token="T", client=fake).responder(555, "Total: $1000", teclado=teclado)

    metodo, payload = fake.llamadas[0]
    assert metodo == "sendMessage"
    assert payload["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "Efectivo", "callback_data": "pago:efectivo"}],
            [{"text": "Cancelar", "callback_data": "venta:cancelar"}],
        ]
    }


async def test_notificador_answer_callback():
    fake = _FakeCliente([{"ok": True}])
    await TelegramNotificador(bot_token="T", client=fake).answer_callback("cb-7")

    metodo, payload = fake.llamadas[0]
    assert metodo == "answerCallbackQuery"
    assert payload["callback_query_id"] == "cb-7"


def test_metodo_pago_incluye_datafono():
    assert "datafono" in get_args(MetodoPago)
