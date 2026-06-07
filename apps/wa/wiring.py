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

# Nombre del job ARQ del eco (registrado en apps.worker.main).
JOB_ECO = "responder_eco_wa"


class ControlWaResolver:
    """Resuelve el tenant por `phone_number_id` abriendo una sesión de control FRESCA por llamada."""

    async def por_phone_number_id(self, phone_number_id: str) -> ResolvedTenant | None:
        async with control_session() as cs:
            return await resolve_tenant_by_wa_number(cs, phone_number_id)


class RedisWaDedup:
    """Dedup por id de mensaje (global): `SET NX EX wa:dedup:{message_id}`. Cliente Redis perezoso."""

    def __init__(self, *, url: str, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    async def marcar_si_nuevo(self, message_id: str) -> bool:
        cliente = self._client or _cliente_redis(self._url)
        marcado = await cliente.set(f"wa:dedup:{message_id}", "1", nx=True, ex=_TTL_DEDUP)
        return bool(marcado)


class ProcesadorEco:
    """Procesa el mensaje encolando un eco en ARQ (no envía en el hilo del webhook).

    Usa `ctx.cliente_telefono` (la identidad que vino del payload) y el `phone_number_id` del mensaje:
    así el destino del eco sale del mismo Contexto del pack, nunca de otra fuente.
    """

    def __init__(self, encolar) -> None:
        self._encolar = encolar  # Callable[..., Awaitable] (arq_pool.enqueue_job)

    async def __call__(self, mensaje: MensajeWa, ctx: Contexto) -> None:
        await self._encolar(
            JOB_ECO, ctx.tenant_id, mensaje.phone_number_id, ctx.cliente_telefono, mensaje.texto
        )


def construir_wa_deps(arq_pool: Any) -> WaDeps:
    """Arma `WaDeps` con los adaptadores reales (lo llama el lifespan del API, con su pool ARQ)."""
    s = get_settings()
    return WaDeps(
        webhook_secret=s.kapso_webhook_secret or None,
        resolver=ControlWaResolver(),
        dedup=RedisWaDedup(url=s.redis_url),
        procesar=ProcesadorEco(encolar=arq_pool.enqueue_job),
    )


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)
