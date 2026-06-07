"""Puertos y tipos del transporte del bot (webhook `/tg/{slug}`).

El webhook depende de estos `Protocol`s, no de implementaciones concretas — igual que el factory
del LLM depende de `ConfigStore`/`KeyStore`. Así el orquestador del turno (`manejar_update`) se
prueba con fakes y la regla de seguridad (validar el secret-token ANTES de tocar la base del
tenant) se verifica sin red ni Postgres. Las implementaciones reales viven en `apps.bot.repos`
(control DB / base del tenant) y en los puertos de Redis/Telegram de entregables posteriores.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto
from core.tenancy.context import ResolvedTenant
from core.voz.transcriptor import Transcriptor


@dataclass(frozen=True, slots=True)
class UpdateBot:
    """Update de Telegram parseado a lo mínimo que el bot necesita (sin depender del SDK)."""

    update_id: int
    chat_id: int
    telegram_id: int                  # id del usuario de Telegram → usuarios.telegram_id
    texto: str | None = None
    voz_file_id: str | None = None     # nota de voz (se transcribe en el entregable 5)


@dataclass(frozen=True, slots=True)
class CallbackBot:
    """callback_query de Telegram parseado a lo mínimo: la pulsación de un botón inline.

    Las mismas validaciones que un mensaje (secret, tenant, dedup, usuario) aplican IGUAL a un
    callback; el `callback_id` se usa luego en `answerCallbackQuery` (quita el "reloj" del botón)."""

    callback_id: str                  # id para answerCallbackQuery
    chat_id: int
    telegram_id: int                  # id del usuario de Telegram → usuarios.telegram_id
    data: str                         # callback_data del botón (p. ej. "pago:efectivo")


@dataclass(frozen=True, slots=True)
class UsuarioBot:
    """Usuario de la empresa mapeado desde `telegram_id` (base del tenant)."""

    id: int
    rol: str                           # vendedor | admin | super_admin (core.auth.rbac)
    activo: bool


class Accion(str, Enum):
    """Desenlace de un update; el adaptador HTTP lo mapea a un status."""

    PROCESADO = "procesado"
    DUPLICADO = "duplicado"                   # update_id repetido (reintento del webhook)
    NO_AUTORIZADO = "no_autorizado"           # telegram_id sin usuario activo en la empresa
    EMPRESA_NO_ENCONTRADA = "empresa_no_encontrada"
    EMPRESA_INACTIVA = "empresa_inactiva"
    SECRET_INVALIDO = "secret_invalido"       # secret-token ausente o que no coincide
    UPDATE_IGNORADO = "update_ignorado"       # update sin mensaje procesable


@dataclass(frozen=True, slots=True)
class ResultadoWebhook:
    """Resultado de orquestar un update. `ctx` solo se puebla cuando se procesó (observabilidad)."""

    accion: Accion
    status: int
    ctx: Contexto | None = None


class ResolverTenant(Protocol):
    """Resuelve la empresa por el slug de la ruta (control DB + caché)."""

    async def por_slug(self, slug: str) -> ResolvedTenant | None: ...


class SecretosBot(Protocol):
    """Secretos por empresa, cifrados en el control DB. Nunca en código (regla #5)."""

    async def webhook_secret(self, empresa_id: int) -> str | None: ...
    async def bot_token(self, empresa_id: int) -> str | None: ...


class CapacidadesStore(Protocol):
    """Features efectivas de la empresa (plan ± overrides); viajan en el contexto (feature-flags §)."""

    async def efectivas(self, empresa_id: int) -> frozenset[str]: ...


class DedupStore(Protocol):
    """Dedup de updates (Redis). Telegram reintenta el webhook si no recibe 200 a tiempo."""

    async def marcar_si_nuevo(self, tenant_id: int, update_id: int) -> bool:
        """True si el update es nuevo (se marca y se procesa); False si ya se vio (descartar)."""
        ...


class UsuariosBotRepo(Protocol):
    """Mapeo telegram_id → usuario, sobre la sesión del tenant."""

    async def por_telegram_id(self, telegram_id: int) -> UsuarioBot | None: ...


# Teclado inline: filas de botones, cada botón un par (texto visible, callback_data). Faked en tests.
Teclado = Sequence[Sequence[tuple[str, str]]]


class Notificador(Protocol):
    """Envía respuestas al chat (Bot API por empresa). Faked en tests: cero red."""

    async def responder(self, chat_id: int, texto: str, *, teclado: Teclado | None = None) -> None:
        """Envía un mensaje; con `teclado` adjunta una botonera inline (sendMessage + reply_markup)."""
        ...

    async def answer_callback(self, callback_id: str, *, texto: str | None = None) -> None:
        """Confirma la pulsación de un botón (answerCallbackQuery): quita el "reloj" del botón."""
        ...


class ArchivosTelegram(Protocol):
    """Descarga archivos de Telegram (nota de voz) usando el bot-token por empresa.

    La implementación real (httpx contra la Bot API: getFile + descarga) vive en el composition
    root, atada al token de la empresa; aquí solo el puerto, faked en tests (cero red)."""

    async def descargar(self, file_id: str) -> bytes: ...


class RecursosEmpresa(Protocol):
    """Los tres adaptadores atados a la credencial de UNA empresa (notificador, voz, archivos).

    Es el *shape* que `ai.turno` consume en la rama de voz sin depender de la impl concreta
    (`apps.bot.recursos.RecursosEmpresa`): un bundle por empresa, ya cableado a su bot-token y su
    api-key. Faked en tests."""

    notificador: Notificador
    transcriptor: Transcriptor
    archivos: ArchivosTelegram


class RecursosBot(Protocol):
    """Resuelve (y cachea) los recursos por empresa. Una sola app sirve N empresas, cada una con su
    bot-token y su api-key; los adaptadores se atan a UNA credencial al construirse, así que hay que
    pedir el bundle de la empresa ANTES de usarlo. Espeja `core.db.engine_cache.EngineCache`."""

    async def para(self, empresa_id: int) -> RecursosEmpresa: ...


# Abre una sesión atada a la base del tenant (CM). En prod envuelve core.db.session.tenant_session.
SesionTenant = Callable[[ResolvedTenant], AbstractAsyncContextManager[AsyncSession]]
# Construye el repo de usuarios sobre una sesión del tenant.
UsuariosFactory = Callable[[AsyncSession], UsuariosBotRepo]
# Maneja un turno ya autenticado (bucle del agente; entregable 2). Aquí solo se invoca.
TurnoHandler = Callable[[UpdateBot, Contexto, AsyncSession, Notificador], Awaitable[None]]
# Maneja un callback (pulsación de botón) ya autenticado. Mismas validaciones que un turno.
CallbackHandler = Callable[[CallbackBot, Contexto, AsyncSession, Notificador], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BotDeps:
    """Todo lo que el webhook necesita, inyectado por puertos. Testeable con fakes."""

    resolver: ResolverTenant
    secretos: SecretosBot
    capacidades: CapacidadesStore
    dedup: DedupStore
    abrir_sesion: SesionTenant
    usuarios: UsuariosFactory
    recursos: RecursosBot
    procesar: TurnoHandler
    # Maneja callbacks (botones). Opcional: un despliegue sin botones deja el flujo de texto intacto.
    procesar_callback: CallbackHandler | None = None
