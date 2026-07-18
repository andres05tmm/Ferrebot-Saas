"""Puertos y tipos del canal Telegram público (webhook del agente de clientes).

Espejo de `apps/wa/ports.py`: el orquestador del webhook (`apps.tg_publico.webhook.manejar_update_tg`)
depende de estos `Protocol`s, no de implementaciones concretas — así se prueba con fakes (sin red, sin
Redis, sin control DB) la regla de seguridad: validar el secret-token ANTES de procesar, dedup por
`update_id` y resolver el tenant por slug. Las implementaciones reales viven en `apps.tg_publico.wiring`
y `apps.tg_publico.repos`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ai.envelope import Contexto
from core.tenancy.context import ResolvedTenant


@dataclass(frozen=True, slots=True)
class UpdateTgPublico:
    """Update de Telegram parseado a lo mínimo del canal (mensaje de texto en chat privado)."""

    update_id: int           # nivel superior del update — base del dedup (reintentos del webhook)
    chat_id: int             # message.chat.id — identidad del cliente ("tg:{chat_id}") y destino saliente
    texto: str               # message.text


class TgTenantResolver(Protocol):
    """Resuelve la empresa por el slug de la URL del webhook (`/tg-publico/{slug}`)."""

    async def por_slug(self, slug: str) -> ResolvedTenant | None: ...


class TgSecretos(Protocol):
    """Lee (descifrado) el secret-token del webhook de una empresa desde el control DB."""

    async def webhook_secret(self, empresa_id: int) -> str | None: ...


class TgDedup(Protocol):
    """Dedup por `(tenant, update_id)`: un reintento del webhook de Telegram no se procesa dos veces."""

    async def marcar_si_nuevo(self, tenant_id: int, update_id: int) -> bool: ...
    async def desmarcar(self, tenant_id: int, update_id: int) -> None: ...


# Procesa un update ya validado: recibe el update y el Contexto público (tenant + cliente_telefono).
# Encola el turno del agente en ARQ (no corre el LLM en el hilo del webhook).
ProcesadorTg = Callable[[UpdateTgPublico, Contexto], Awaitable[None]]


class AccionTg(str, Enum):
    """Desenlace de un webhook entrante; el adaptador HTTP lo mapea a un status."""

    PROCESADO = "procesado"
    DUPLICADO = "duplicado"                   # update_id repetido (reintento del webhook)
    NO_MAPEADO = "no_mapeado"                 # slug sin empresa (200: no dispara retry-storm de Telegram)
    EMPRESA_INACTIVA = "empresa_inactiva"     # el slug mapea a una empresa no activa
    SECRET_INVALIDO = "secret_invalido"       # X-Telegram-Bot-Api-Secret-Token ausente o distinto (403)
    UPDATE_IGNORADO = "update_ignorado"       # no es mensaje de texto en chat privado
    BODY_INVALIDO = "body_invalido"           # cuerpo JSON inválido


@dataclass(frozen=True, slots=True)
class ResultadoTg:
    """Resultado de un webhook: la acción, el status HTTP y, si procesó, el Contexto construido."""

    accion: AccionTg
    status: int
    ctx: Contexto | None = None


@dataclass(frozen=True, slots=True)
class TgPublicoDeps:
    """Dependencias del webhook (inyectadas por el composition root)."""

    resolver: TgTenantResolver
    secretos: TgSecretos
    dedup: TgDedup
    procesar: ProcesadorTg
