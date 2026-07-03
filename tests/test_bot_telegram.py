"""CR-1 — adaptadores HTTP de Telegram (con cliente FAKE: cero red, cero SDK).

Pin del contrato:
  - `responder` hace UN `sendMessage` con `{chat_id, text}` y lanza si la respuesta no es ok;
  - `descargar` hace `getFile`, arma la URL de descarga con el `file_path` y baja los bytes;
    lanza si `getFile` falla o no trae `file_path`.
El HTTP real queda fuera del camino testeable (el fake captura lo que recibió).
"""
import pytest

from apps.bot.telegram import TelegramArchivos, TelegramError, TelegramNotificador


class FakeCliente:
    """Cliente de Telegram falso: devuelve respuestas pre-cargadas y captura lo recibido."""

    def __init__(self, *, respuestas: list[dict] | None = None, binario: bytes = b"") -> None:
        self._respuestas = list(respuestas or [])
        self._binario = binario
        self.llamadas: list[tuple[str, dict]] = []
        self.descargas: list[str] = []

    async def call(self, metodo: str, payload: dict) -> dict:
        self.llamadas.append((metodo, payload))
        return self._respuestas.pop(0)

    async def download(self, url: str) -> bytes:
        self.descargas.append(url)
        return self._binario


# ------------------------------- responder --------------------------------

async def test_responder_envia_sendmessage_con_payload():
    fake = FakeCliente(respuestas=[{"ok": True, "result": {"message_id": 1}}])
    await TelegramNotificador(bot_token="TOKEN", client=fake).responder(555, "Hola 👋")

    assert fake.llamadas == [("sendMessage", {"chat_id": 555, "text": "Hola 👋"})]


async def test_responder_lanza_si_respuesta_no_ok():
    fake = FakeCliente(respuestas=[{"ok": False, "description": "chat not found"}])
    with pytest.raises(TelegramError):
        await TelegramNotificador(bot_token="TOKEN", client=fake).responder(555, "Hola")


# ------------------------------- descargar --------------------------------

async def test_descargar_getfile_y_baja_bytes():
    fake = FakeCliente(
        respuestas=[{"ok": True, "result": {"file_id": "F", "file_path": "voice/file_1.oga"}}],
        binario=b"OGG-OPUS-BYTES",
    )
    data = await TelegramArchivos(bot_token="TOKEN", client=fake).descargar("F")

    assert data == b"OGG-OPUS-BYTES"
    assert fake.llamadas == [("getFile", {"file_id": "F"})]
    assert fake.descargas == ["https://api.telegram.org/file/botTOKEN/voice/file_1.oga"]


async def test_descargar_lanza_si_no_hay_file_path():
    fake = FakeCliente(respuestas=[{"ok": True, "result": {}}])
    with pytest.raises(TelegramError):
        await TelegramArchivos(bot_token="TOKEN", client=fake).descargar("F")
    assert fake.descargas == []          # no intenta bajar nada sin file_path


async def test_descargar_lanza_si_getfile_error():
    fake = FakeCliente(respuestas=[{"ok": False, "description": "file not found"}])
    with pytest.raises(TelegramError):
        await TelegramArchivos(bot_token="TOKEN", client=fake).descargar("F")


# --------------------- cliente real: URL con token redactada ---------------

class _RespuestaFalsa:
    """`httpx.AsyncClient` falso: fuerza el error HTTP para verificar la redacción del token."""

    def __init__(self, *a, **kw): ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        import httpx

        return httpx.Response(404, request=httpx.Request("GET", url))

    async def post(self, url, json=None):
        import httpx

        raise httpx.ConnectError("conexión rechazada", request=httpx.Request("POST", url))


async def test_download_real_redacta_token_en_error_http(monkeypatch):
    import httpx

    from apps.bot.telegram import TelegramHTTPError, _cliente_telegram

    monkeypatch.setattr(httpx, "AsyncClient", _RespuestaFalsa)
    cliente = _cliente_telegram("TOKEN-SECRETO")

    with pytest.raises(TelegramHTTPError) as ei:
        await cliente.download("https://api.telegram.org/file/botTOKEN-SECRETO/voice/x.oga")

    assert "TOKEN-SECRETO" not in str(ei.value)
    assert "404" in str(ei.value)
    assert ei.value.__suppress_context__          # la excepción de httpx (con URL) no encadena


async def test_call_real_redacta_token_en_error_de_red(monkeypatch):
    import httpx

    from apps.bot.telegram import TelegramHTTPError, _cliente_telegram

    monkeypatch.setattr(httpx, "AsyncClient", _RespuestaFalsa)
    cliente = _cliente_telegram("TOKEN-SECRETO")

    with pytest.raises(TelegramHTTPError) as ei:
        await cliente.call("sendMessage", {"chat_id": 1, "text": "hola"})

    assert "TOKEN-SECRETO" not in str(ei.value)
    assert "sendMessage" in str(ei.value)
    assert ei.value.__suppress_context__
