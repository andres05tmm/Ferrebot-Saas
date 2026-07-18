"""Adaptadores HTTP de la Bot API de Telegram (satisfacen los puertos de `apps.bot.ports`).

Mismo patrón que `core/llm/providers/openai.py`: el HTTP se aísla tras un **cliente inyectable**
(`ClienteTelegram`), con impl real PEREZOSA construida desde el `bot_token`. Importar este módulo
NO importa `httpx` ni abre red; la lógica testeable (armar la request, validar la respuesta, construir
la URL de descarga) queda separada de la llamada HTTP real, que los tests reemplazan con un fake.

Cada adaptador se ata a UN `bot_token` al construirse; el enlace token↔empresa es trabajo de CR-3.
"""
from __future__ import annotations

from typing import Any, Protocol

from apps.bot.ports import Teclado
from core.logging import get_logger

log = get_logger("bot.telegram")

# URL base de descarga de archivos de Telegram (distinta del endpoint de métodos de la Bot API).
_FILE_BASE = "https://api.telegram.org/file/bot{token}/{path}"


class TelegramError(Exception):
    """Fallo de la Bot API (no-2xx, `ok: false` o respuesta sin los campos esperados)."""


class TelegramHTTPError(TelegramError):
    """Fallo HTTP contra Telegram con la URL redactada: solo método y status/tipo de error.

    Las excepciones de httpx llevan la URL completa (incluye el bot token) en el mensaje; esta
    clase las reemplaza SIN encadenar (`from None`) para que el token no suba a logs/Sentry."""


class ClienteTelegram(Protocol):
    """Borde HTTP de Telegram: un método JSON (`call`) y una descarga binaria (`download`).

    Faked en tests (cero red); en prod lo construye `_cliente_telegram(bot_token)` con httpx."""

    async def call(self, metodo: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def download(self, url: str) -> bytes: ...


class TelegramNotificador:
    """Envía mensajes al chat vía `sendMessage`. Satisface `apps.bot.ports.Notificador`."""

    def __init__(self, *, bot_token: str, client: ClienteTelegram | None = None) -> None:
        self._bot_token = bot_token
        self._client = client

    async def responder(
        self, chat_id: int, texto: str, *, teclado: Teclado | None = None,
        parse_mode: str | None = None,
    ) -> None:
        client = self._client or _cliente_telegram(self._bot_token)
        payload: dict[str, Any] = {"chat_id": chat_id, "text": texto}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if teclado is not None:
            payload["reply_markup"] = _inline_keyboard(teclado)
        raw = await client.call("sendMessage", payload)
        if not raw.get("ok"):
            raise TelegramError(raw.get("description") or "sendMessage falló")

    async def answer_callback(self, callback_id: str, *, texto: str | None = None) -> None:
        client = self._client or _cliente_telegram(self._bot_token)
        payload: dict[str, Any] = {"callback_query_id": callback_id}
        if texto is not None:
            payload["text"] = texto
        raw = await client.call("answerCallbackQuery", payload)
        if not raw.get("ok"):
            raise TelegramError(raw.get("description") or "answerCallbackQuery falló")


def _inline_keyboard(teclado: Teclado) -> dict[str, Any]:
    """Filas de (texto, callback_data) → `reply_markup` con `inline_keyboard` de la Bot API."""
    return {
        "inline_keyboard": [
            [{"text": texto, "callback_data": data} for texto, data in fila]
            for fila in teclado
        ]
    }


class TelegramArchivos:
    """Descarga notas de voz: `getFile` → `file_path` → descarga binaria. Satisface `ArchivosTelegram`."""

    def __init__(self, *, bot_token: str, client: ClienteTelegram | None = None) -> None:
        self._bot_token = bot_token
        self._client = client

    async def descargar(self, file_id: str) -> bytes:
        client = self._client or _cliente_telegram(self._bot_token)
        raw = await client.call("getFile", {"file_id": file_id})
        file_path = (raw.get("result") or {}).get("file_path")
        if not raw.get("ok") or not file_path:
            raise TelegramError(raw.get("description") or "getFile sin file_path")
        url = _FILE_BASE.format(token=self._bot_token, path=file_path)
        return await client.download(url)


def _cliente_telegram(bot_token: str) -> ClienteTelegram:
    """Cliente real (perezoso): importa httpx solo al invocar, no al cargar el módulo."""

    class _HttpxCliente:
        async def call(self, metodo: str, payload: dict[str, Any]) -> dict[str, Any]:
            import httpx

            url = f"https://api.telegram.org/bot{bot_token}/{metodo}"
            try:
                async with httpx.AsyncClient() as cliente:
                    resp = await cliente.post(url, json=payload)
            except httpx.HTTPError as exc:
                log.warning("telegram_http_error", metodo=metodo, error=type(exc).__name__)
                raise TelegramHTTPError(f"{metodo}: {type(exc).__name__}") from None
            return resp.json()

        async def download(self, url: str) -> bytes:
            import httpx

            log.info("telegram_descarga_archivo")   # nunca la URL: lleva el token
            try:
                async with httpx.AsyncClient() as cliente:
                    resp = await cliente.get(url)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                log.warning("telegram_descarga_error", status=exc.response.status_code)
                raise TelegramHTTPError(f"GET archivo: HTTP {exc.response.status_code}") from None
            except httpx.HTTPError as exc:
                log.warning("telegram_descarga_error", error=type(exc).__name__)
                raise TelegramHTTPError(f"GET archivo: {type(exc).__name__}") from None
            return resp.content

    return _HttpxCliente()
