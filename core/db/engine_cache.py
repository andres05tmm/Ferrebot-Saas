"""Caché de engines async por empresa, con evicción LRU (tenancy.md §4).

Pool pequeño por empresa (PgBouncer multiplexa lo pesado). Sin prepared statements del
lado servidor para ser compatible con PgBouncer en modo transaction (tenancy.md §5).
"""
import asyncio
from collections import OrderedDict

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# asyncpg: deshabilitar caché de statements -> compatible con PgBouncer transaction mode.
_CONNECT_ARGS = {"statement_cache_size": 0}


class EngineCache:
    """Mapa tenant_id -> AsyncEngine, creado perezosamente, con tope LRU."""

    def __init__(self, max_engines: int = 200) -> None:
        self._max = max_engines
        self._engines: OrderedDict[int, AsyncEngine] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get_or_create(self, tenant_id: int, async_url: str) -> AsyncEngine:
        async with self._lock:
            engine = self._engines.get(tenant_id)
            if engine is not None:
                self._engines.move_to_end(tenant_id)
                return engine
            engine = create_async_engine(
                async_url,
                pool_size=2,
                max_overflow=2,
                pool_pre_ping=True,
                connect_args=_CONNECT_ARGS,
            )
            self._engines[tenant_id] = engine
            await self._evict_if_needed()
            return engine

    async def _evict_if_needed(self) -> None:
        while len(self._engines) > self._max:
            _, victim = self._engines.popitem(last=False)
            await victim.dispose()

    async def dispose_all(self) -> None:
        async with self._lock:
            for engine in self._engines.values():
                await engine.dispose()
            self._engines.clear()


# Caché global del proceso (uno por instancia de API).
engine_cache = EngineCache()
