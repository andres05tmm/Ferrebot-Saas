"""Endpoint SSE acotado a la empresa del request (architecture.md §12, tenancy.md §6)."""
import asyncio
from collections.abc import AsyncIterator

from core.events.hub import event_hub
from core.tenancy.context import ResolvedTenant

_KEEPALIVE_SECONDS = 25.0


async def tenant_event_stream(tenant: ResolvedTenant) -> AsyncIterator[dict]:
    """Genera eventos SSE (formato sse-starlette) solo de la base de `tenant`."""
    queue = await event_hub.subscribe(tenant.id, tenant.connection_url)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
                yield {"event": "message", "data": payload}
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": ""}
    finally:
        await event_hub.unsubscribe(tenant.id, queue)
