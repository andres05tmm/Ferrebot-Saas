"""Sender saliente del canal Telegram público, con la interfaz que `AgenteWa` espera de `KapsoSender`.

`AgenteWa._enviar` llama `sender.enviar_texto(phone_number_id=, to=, texto=)`. Aquí se adapta a Telegram
reusando `apps.bot.telegram.TelegramNotificador` (`sendMessage`), con dos convenciones del canal:

  - `phone_number_id` transporta el TENANT_ID: el token del bot es POR TENANT (se lee cifrado del control
    DB, clave `tg_publico_bot_token`); no hay `phone_number_id` de Kapso en Telegram.
  - `to` es `"tg:{chat_id}"` (la identidad del cliente que el webhook fija desde el payload).

Cachea un notificador por tenant (get-or-create bajo lock) para no releer el token cifrado en cada envío
—mismo patrón que la caché de clientes MATIAS del worker—. El token se resuelve perezoso (`resolver_token`,
inyectable en tests). `AgenteWa._enviar` ya envuelve el envío en try/except, así que un token faltante o
un fallo de red no tumban el job (solo se registra).
"""
from __future__ import annotations

import asyncio
import html
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from apps.bot.telegram import TelegramError, TelegramHTTPError, TelegramNotificador
from apps.tg_publico.repos import SecretosTgPublico
from core.db.session import control_session
from core.logging import get_logger

log = get_logger("tg_publico.sender")


async def enviar_foto(token: str, chat_id: int, foto: str, *, caption: str | None = None) -> None:
    """`sendPhoto` con una URL pública (Telegram la descarga) o un archivo local (multipart).

    Errores redactados como en `apps.bot.telegram`: la URL de httpx lleva el token → `from None`.
    """
    import httpx

    datos: dict[str, object] = {"chat_id": chat_id}
    if caption:
        datos["caption"] = caption
    es_url = foto.startswith(("http://", "https://"))
    try:
        async with httpx.AsyncClient(timeout=30.0) as cliente:
            if es_url:
                resp = await cliente.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    json={**datos, "photo": foto},
                )
            else:
                resp = await cliente.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data=datos, files={"photo": (Path(foto).name, Path(foto).read_bytes())},
                )
    except httpx.HTTPError as exc:
        raise TelegramHTTPError(f"sendPhoto: {type(exc).__name__}") from None
    data = resp.json()
    if not data.get("ok"):
        raise TelegramError(data.get("description") or "sendPhoto falló")


class TokenTgFaltante(RuntimeError):
    """El tenant no tiene `tg_publico_bot_token` configurado: no se puede enviar por Telegram."""


# `*negrita*` de una sola línea (formato WhatsApp que produce `whatsappify`) → <b> de Telegram.
_RE_NEGRITA = re.compile(r"\*([^*\n]+)\*")

# Fila de tabla Markdown (`| a | b |`) y fila separadora (`|---|---|`): los chats no renderizan
# tablas — se convierten a viñetas ANTES de enviar, pase lo que pase con el prompt.
_RE_FILA_TABLA = re.compile(r"^\s*\|(.+)\|\s*$")
_RE_FILA_SEPARADORA = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")


def sin_tablas(texto: str) -> str:
    """Tablas Markdown → viñetas legibles (`• a — b`). Texto sin tablas pasa intacto."""
    lineas = []
    for linea in texto.splitlines():
        if _RE_FILA_SEPARADORA.match(linea):
            continue
        m = _RE_FILA_TABLA.match(linea)
        if m:
            celdas = [c.strip() for c in m.group(1).split("|") if c.strip()]
            if celdas:
                lineas.append("• " + " — ".join(celdas))
            continue
        lineas.append(linea)
    return "\n".join(lineas)


def telegramify(texto: str) -> str:
    """Texto estilo WhatsApp (`*negrita*`, salida de `whatsappify`) → HTML de Telegram.

    Telegram sin `parse_mode` muestra los asteriscos LITERALES; con `parse_mode=HTML` y el texto
    escapado la conversión es determinista (no hay entidades Markdown a medio balancear que hagan
    fallar `sendMessage`). Solo negrita: es lo único que `whatsappify` deja marcado.
    """
    return _RE_NEGRITA.sub(r"<b>\1</b>", html.escape(texto, quote=False))


class TelegramPublicoSender:
    """Envío saliente por la Bot API de Telegram con el token cifrado por tenant (interfaz `KapsoSender`)."""

    def __init__(
        self,
        master_key: str,
        *,
        resolver_token: Callable[[int], Awaitable[str | None]] | None = None,
        notificador_factory: Callable[..., TelegramNotificador] = TelegramNotificador,
    ) -> None:
        self._master = master_key
        self._resolver_token = resolver_token or self._leer_token
        self._factory = notificador_factory
        self._cache: dict[int, TelegramNotificador] = {}
        self._lock = asyncio.Lock()

    async def _leer_token(self, tenant_id: int) -> str | None:
        async with control_session() as cs:
            return await SecretosTgPublico(cs, self._master).bot_token(tenant_id)

    async def _notificador(self, tenant_id: int) -> TelegramNotificador:
        async with self._lock:
            notificador = self._cache.get(tenant_id)
            if notificador is None:
                token = await self._resolver_token(tenant_id)
                if not token:
                    raise TokenTgFaltante(f"tenant {tenant_id} sin {SecretosTgPublico.__name__} token")
                notificador = self._factory(bot_token=token)
                self._cache[tenant_id] = notificador
            return notificador

    async def enviar_texto(self, *, phone_number_id: str, to: str, texto: str) -> None:
        """Envía un mensaje de texto por Telegram. `phone_number_id`=tenant_id, `to`="tg:{chat_id}".

        Negrita real vía HTML (`telegramify`); si Telegram rechaza el formato por cualquier borde,
        se reintenta EN PLANO — el mensaje siempre sale.
        """
        tenant_id = int(phone_number_id)
        chat_id = int(to.removeprefix("tg:"))
        notificador = await self._notificador(tenant_id)
        plano = sin_tablas(texto)
        try:
            await notificador.responder(chat_id, telegramify(plano), parse_mode="HTML")
        except TelegramError:
            log.warning("tg_html_rechazado_fallback_plano", tenant_id=tenant_id)
            await notificador.responder(chat_id, plano)
