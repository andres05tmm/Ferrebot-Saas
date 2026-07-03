"""Composition root del canal WhatsApp: arma `WaDeps` con los adaptadores reales.

Smoke manual (como `apps.worker.main`): la lógica vive en `apps.wa.webhook`/`apps.wa.kapso` (testeable
con fakes). Aquí solo el cableado: resolver de control DB, dedup en Redis (perezoso) y el procesador
de ECO que ENCOLA el envío en ARQ (no bloquea el webhook). El bucle del agente reemplazará al
procesador de eco en el siguiente entregable.
"""
from __future__ import annotations

from typing import Any

from apps.wa.kapso import MensajeWa
from apps.wa.ports import WaDeps
from ai.envelope import Contexto
from core.config import get_settings
from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.context import ResolvedTenant
from core.tenancy.control_repo import resolve_tenant_by_wa_number

log = get_logger("wa.wiring")

_TTL_DEDUP = 86_400  # 24h: cubre de sobra los reintentos del webhook de Kapso

# Nombre del job ARQ del agente (registrado en apps.worker.main): el webhook encola, el worker atiende.
JOB_AGENTE = "atender_mensaje_wa"


class ControlWaResolver:
    """Resuelve el tenant por `phone_number_id` abriendo una sesión de control FRESCA por llamada."""

    async def por_phone_number_id(self, phone_number_id: str) -> ResolvedTenant | None:
        async with control_session() as cs:
            return await resolve_tenant_by_wa_number(cs, phone_number_id)


class RedisWaDedup:
    """Dedup por id de mensaje (global): `SET NX EX wa:dedup:{message_id}`. Cliente Redis perezoso
    (se crea al primer uso y se cachea en la instancia: el pool de redis-py multiplexa)."""

    def __init__(self, *, url: str, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    def _redis(self) -> Any:
        if self._client is None:
            self._client = _cliente_redis(self._url)
        return self._client

    async def marcar_si_nuevo(self, message_id: str) -> bool:
        marcado = await self._redis().set(f"wa:dedup:{message_id}", "1", nx=True, ex=_TTL_DEDUP)
        return bool(marcado)

    async def desmarcar(self, message_id: str) -> None:
        """Libera la marca (encolado fallido): el reintento del proveedor sí se procesa."""
        await self._redis().delete(f"wa:dedup:{message_id}")


class ProcesadorAgente:
    """Procesa el mensaje encolando el turno del agente en ARQ (no corre el LLM en el hilo del webhook).

    Mantiene el webhook rápido (200 inmediato); el worker resuelve el tenant por id y atiende. Pasa el
    `cliente_telefono` DEL CONTEXTO (vino del payload) y el `phone_number_id` del mensaje — la identidad
    del cliente nunca sale de otra fuente.
    """

    def __init__(self, encolar) -> None:
        self._encolar = encolar  # Callable[..., Awaitable] (arq_pool.enqueue_job)

    async def __call__(self, mensaje: MensajeWa, ctx: Contexto) -> None:
        await self._encolar(
            JOB_AGENTE, ctx.tenant_id, mensaje.phone_number_id, ctx.cliente_telefono,
            mensaje.texto, mensaje.message_id,
        )


def construir_wa_deps(arq_pool: Any) -> WaDeps:
    """Arma `WaDeps` con los adaptadores reales (lo llama el lifespan del API, con su pool ARQ)."""
    s = get_settings()
    return WaDeps(
        webhook_secret=s.kapso_webhook_secret or None,
        resolver=ControlWaResolver(),
        dedup=RedisWaDedup(url=s.redis_url),
        procesar=ProcesadorAgente(encolar=arq_pool.enqueue_job),
    )


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)
