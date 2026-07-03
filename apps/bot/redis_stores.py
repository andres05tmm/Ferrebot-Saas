"""Adaptadores Redis: dedup de updates y pendiente de confirmación (CR-2).

Mismo patrón que los clientes httpx de CR-1: el cliente Redis real es PEREZOSO (import dentro de los
métodos; nada de red al cargar el módulo). La (de)serialización del pendiente la hace
`ai.confirmacion` (probada allí, sin Redis); aquí solo se ata a las claves y los TTL.
  - `RedisDedupStore` satisface `apps.bot.ports.DedupStore` (SET NX EX, `dedup:{tenant}:{update_id}`).
  - `RedisConfirmStore` satisface `ai.confirmacion.ConfirmStore` (`confirm:{tenant}:{chat}`, EX ~300s).
"""
from __future__ import annotations

from typing import Any

from ai.confirmacion import Pendiente, _deserializar, _serializar
from core.llm.base import ToolCall
from core.logging import get_logger

log = get_logger("bot.redis")

# TTL del dedup de updates (segundos) y del pendiente de confirmación.
_TTL_DEDUP = 86_400          # 24h: cubre de sobra los reintentos del webhook de Telegram
_TTL_CONFIRM = 300           # ventana para que el usuario diga "sí"


class RedisDedupStore:
    """Dedup de updates de Telegram (un reintento del webhook no se procesa dos veces)."""

    def __init__(self, *, url: str, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    async def marcar_si_nuevo(self, tenant_id: int, update_id: int) -> bool:
        cliente = self._client or _cliente_redis(self._url)
        # SET NX EX: marca la clave solo si no existía → True = update nuevo (procesar).
        marcado = await cliente.set(f"dedup:{tenant_id}:{update_id}", "1", nx=True, ex=_TTL_DEDUP)
        return bool(marcado)

    async def desmarcar(self, tenant_id: int, update_id: int) -> None:
        """Libera la marca (procesamiento fallido): el reintento de Telegram sí se procesa."""
        cliente = self._client or _cliente_redis(self._url)
        await cliente.delete(f"dedup:{tenant_id}:{update_id}")


class RedisConfirmStore:
    """Pendiente de confirmación por (tenant, chat), con TTL (`_TTL_CONFIRM`)."""

    def __init__(self, *, url: str, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    def _key(self, tenant_id: int, chat_id: int) -> str:
        return f"confirm:{tenant_id}:{chat_id}"

    async def guardar(
        self, tenant_id: int, chat_id: int, *, tool_call: ToolCall, idempotency_key: str
    ) -> None:
        cliente = self._client or _cliente_redis(self._url)
        dato = _serializar(Pendiente(tool_call=tool_call, idempotency_key=idempotency_key))
        await cliente.set(self._key(tenant_id, chat_id), dato, ex=_TTL_CONFIRM)

    async def obtener(self, tenant_id: int, chat_id: int) -> Pendiente | None:
        cliente = self._client or _cliente_redis(self._url)
        dato = await cliente.get(self._key(tenant_id, chat_id))
        return _deserializar(dato) if dato else None

    async def borrar(self, tenant_id: int, chat_id: int) -> None:
        cliente = self._client or _cliente_redis(self._url)
        await cliente.delete(self._key(tenant_id, chat_id))


class RedisVentaPendienteStore:
    """Venta pendiente de método de pago por (tenant, chat). Satisface `ai.confirmacion.VentaPendienteStore`.

    Mismo patrón que `RedisConfirmStore` (clave dedicada `venta_pendiente:{tenant}:{chat}`, TTL ~300s,
    (de)serialización de `ai.confirmacion`). RED: esqueleto."""

    def __init__(self, *, url: str, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    def _key(self, tenant_id: int, chat_id: int) -> str:
        return f"venta_pendiente:{tenant_id}:{chat_id}"

    async def guardar(
        self, tenant_id: int, chat_id: int, *, tool_call: ToolCall, idempotency_key: str
    ) -> None:
        cliente = self._client or _cliente_redis(self._url)
        dato = _serializar(Pendiente(tool_call=tool_call, idempotency_key=idempotency_key))
        await cliente.set(self._key(tenant_id, chat_id), dato, ex=_TTL_CONFIRM)

    async def obtener(self, tenant_id: int, chat_id: int) -> Pendiente | None:
        cliente = self._client or _cliente_redis(self._url)
        dato = await cliente.get(self._key(tenant_id, chat_id))
        return _deserializar(dato) if dato else None

    async def borrar(self, tenant_id: int, chat_id: int) -> None:
        cliente = self._client or _cliente_redis(self._url)
        await cliente.delete(self._key(tenant_id, chat_id))


def _cliente_redis(url: str) -> Any:
    """Cliente Redis real (perezoso): importa `redis.asyncio` solo al invocar, no al cargar."""
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)
