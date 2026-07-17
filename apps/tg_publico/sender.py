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
from collections.abc import Awaitable, Callable

from apps.bot.telegram import TelegramNotificador
from apps.tg_publico.repos import SecretosTgPublico
from core.db.session import control_session
from core.logging import get_logger

log = get_logger("tg_publico.sender")


class TokenTgFaltante(RuntimeError):
    """El tenant no tiene `tg_publico_bot_token` configurado: no se puede enviar por Telegram."""


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
        """Envía un mensaje de texto por Telegram. `phone_number_id`=tenant_id, `to`="tg:{chat_id}"."""
        tenant_id = int(phone_number_id)
        chat_id = int(to.removeprefix("tg:"))
        notificador = await self._notificador(tenant_id)
        await notificador.responder(chat_id, texto)
