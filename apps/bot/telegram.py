"""Adaptadores HTTP de la Bot API de Telegram (satisfacen los puertos de `apps.bot.ports`).

Mismo patrón que `core/llm/providers/openai.py`: el HTTP se aísla tras un **cliente inyectable**
(`ClienteTelegram`), con impl real PEREZOSA construida desde el `bot_token`. Importar este módulo
NO importa `httpx` ni abre red; la lógica testeable (armar la request, validar la respuesta, construir
la URL de descarga) queda separada de la llamada HTTP real, que los tests reemplazan con un fake.

Cada adaptador se ata a UN `bot_token` al construirse; el enlace token↔empresa es trabajo de CR-3.
"""
from __future__ import annotations

from typing import Any, Protocol

from core.logging import get_logger

log = get_logger("bot.telegram")

# URL base de descarga de archivos de Telegram (distinta del endpoint de métodos de la Bot API).
_FILE_BASE = "https://api.telegram.org/file/bot{token}/{path}"


class TelegramError(Exception):
    """Fallo de la Bot API (no-2xx, `ok: false` o respuesta sin los campos esperados)."""


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

    async def responder(self, chat_id: int, texto: str) -> None:
        client = self._client or _cliente_telegram(self._bot_token)
        raw = await client.call("sendMessage", {"chat_id": chat_id, "text": texto})
        if not raw.get("ok"):
            raise TelegramError(raw.get("description") or "sendMessage falló")


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
            async with httpx.AsyncClient() as cliente:
                resp = await cliente.post(url, json=payload)
            return resp.json()

        async def download(self, url: str) -> bytes:
            import httpx

            log.info("telegram_descarga_archivo")   # nunca la URL: lleva el token
            async with httpx.AsyncClient() as cliente:
                resp = await cliente.get(url)
            resp.raise_for_status()
            return resp.content

    return _HttpxCliente()
