"""Caché de recursos del bot por empresa (espejo de `core.db.engine_cache.EngineCache`).

El bot es una sola app que sirve N empresas. Los adaptadores de CR-1 (`TelegramNotificador`,
`TelegramArchivos`, `WhisperTranscriptor`) se atan a UNA credencial al construirse y sus métodos
(`responder`/`descargar`/`transcribir`) NO llevan `empresa_id` → hay que construir la instancia de
cada empresa ANTES de llamarla. `RecursosBot` resuelve las credenciales (perezoso, vía `cargar`),
construye el bundle de las tres instancias y lo CACHEA por empresa, con lock para llamar a `cargar`
una sola vez por empresa (igual que el engine cache llama a su loader una sola vez por tenant).

`cargar` se INYECTA: el loader real (abrir sesión de control, `ControlSecretosBot.bot_token` +
`ControlLLMKeyStore.api_key(empresa_id, "openai")`) se cablea en el composition root (CR-3b); aquí
los tests lo falsean (cero SQL, cero red).
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apps.bot.ports import ArchivosTelegram, Notificador
from apps.bot.telegram import TelegramArchivos, TelegramNotificador
from core.voz.transcriptor import Transcriptor, WhisperTranscriptor


@dataclass(frozen=True, slots=True)
class Credenciales:
    """Credenciales por empresa para construir los adaptadores (vienen cifradas del control DB)."""

    bot_token: str | None
    openai_key: str | None


@dataclass(frozen=True, slots=True)
class RecursosEmpresa:
    """Las tres instancias atadas a la credencial de una empresa (satisface `ports.RecursosEmpresa`)."""

    notificador: Notificador
    transcriptor: Transcriptor
    archivos: ArchivosTelegram


class RecursosBot:
    """Mapa `empresa_id -> RecursosEmpresa`, construido perezosamente y cacheado (espejo de
    `EngineCache`). `cargar` resuelve las credenciales y se inyecta; el bundle se construye una sola
    vez por empresa (lock)."""

    def __init__(self, *, cargar: Callable[[int], Awaitable[Credenciales]]) -> None:
        self._cargar = cargar
        self._cache: dict[int, RecursosEmpresa] = {}
        self._lock = asyncio.Lock()

    async def para(self, empresa_id: int) -> RecursosEmpresa:
        """Devuelve (o construye y cachea) los recursos de la empresa. Resuelve credenciales con
        `cargar` —una sola vez por empresa, bajo lock— y arma TelegramNotificador/TelegramArchivos
        (bot_token) + WhisperTranscriptor (openai_key). El lock se mantiene a través de la carga,
        como `EngineCache.get_or_create`, para no construir el bundle dos veces."""
        async with self._lock:
            bundle = self._cache.get(empresa_id)
            if bundle is not None:
                return bundle
            cred = await self._cargar(empresa_id)
            bundle = RecursosEmpresa(
                notificador=TelegramNotificador(bot_token=cred.bot_token),
                transcriptor=WhisperTranscriptor(api_key=cred.openai_key),
                archivos=TelegramArchivos(bot_token=cred.bot_token),
            )
            self._cache[empresa_id] = bundle
            return bundle
