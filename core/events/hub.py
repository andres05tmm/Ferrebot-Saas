"""Hub de eventos en tiempo real, un listener por empresa con suscriptores SSE (tenancy.md §6).

El LISTEN necesita una conexión de SESIÓN persistente, que NO funciona sobre PgBouncer en
modo transaction. Por eso cada listener abre una conexión DIRECTA a Postgres (asyncpg nativo).
Sin suscriptores no hay listener.
"""
import asyncio

import asyncpg

from core.events.publisher import CHANNEL
from core.logging import get_logger

log = get_logger("events")


class _TenantListener:
    """Una conexión directa de LISTEN para una empresa y sus colas de suscriptores."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: asyncpg.Connection | None = None
        self._subscribers: set[asyncio.Queue[str]] = set()

    async def start(self) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        await self._conn.add_listener(CHANNEL, self._on_notify)

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        for queue in self._subscribers:
            queue.put_nowait(payload)

    def add(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.add(queue)

    def remove(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    @property
    def empty(self) -> bool:
        return not self._subscribers

    async def stop(self) -> None:
        if self._conn is not None:
            await self._conn.remove_listener(CHANNEL, self._on_notify)
            await self._conn.close()
            self._conn = None


class TenantEventHub:
    """Gestiona listeners por tenant_id; crea/cierra conexiones según haya suscriptores."""

    def __init__(self) -> None:
        self._listeners: dict[int, _TenantListener] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, tenant_id: int, dsn: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue()
        async with self._lock:
            listener = self._listeners.get(tenant_id)
            if listener is None:
                listener = _TenantListener(dsn)
                await listener.start()
                self._listeners[tenant_id] = listener
                log.info("listener_iniciado", tenant_id=tenant_id)
            listener.add(queue)
        return queue

    async def unsubscribe(self, tenant_id: int, queue: asyncio.Queue[str]) -> None:
        async with self._lock:
            listener = self._listeners.get(tenant_id)
            if listener is None:
                return
            listener.remove(queue)
            if listener.empty:
                await listener.stop()
                del self._listeners[tenant_id]
                log.info("listener_detenido", tenant_id=tenant_id)

    async def dispose_all(self) -> None:
        async with self._lock:
            for listener in self._listeners.values():
                await listener.stop()
            self._listeners.clear()


event_hub = TenantEventHub()
