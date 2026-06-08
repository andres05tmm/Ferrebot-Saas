"""Hub de eventos en tiempo real, un listener por empresa con suscriptores SSE (tenancy.md §6).

El LISTEN necesita una conexión de SESIÓN persistente, que NO funciona sobre PgBouncer en
modo transaction. Por eso cada listener abre una conexión DIRECTA a Postgres (asyncpg nativo).
Sin suscriptores no hay listener.

Endurecido contra la caída silenciosa de esa conexión de larga vida (idle TCP drop en redes
internas): el `_TenantListener` se RECONECTA solo (termination listener de asyncpg + un keepalive
periódico que detecta la conexión muerta), PRESERVANDO las colas de los suscriptores vivos, y deja
trazas estructuradas (start, evento recibido, terminación, reconexión) para diagnosticar en prod.
Sin secretos en los logs (nunca el DSN). API pública del hub intacta.
"""
import asyncio

import asyncpg

from core.events.publisher import CHANNEL
from core.logging import get_logger

log = get_logger("events")

# Cada cuánto se hace un SELECT 1 sobre la conexión del listener (evita que la red la corte por idle).
_KEEPALIVE_S = 30.0
# Tope del backoff exponencial entre reintentos de reconexión.
_BACKOFF_MAX_S = 30.0


class _TenantListener:
    """Una conexión directa de LISTEN para una empresa y sus colas de suscriptores (auto-reconecta)."""

    def __init__(self, tenant_id: int, dsn: str) -> None:
        self._tenant_id = tenant_id
        self._dsn = dsn
        self._conn: asyncpg.Connection | None = None
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._lock = asyncio.Lock()                       # serializa las reconexiones
        self._keepalive: asyncio.Task | None = None
        self._cerrado = False                             # stop() pedido → no reconectar

    async def start(self) -> None:
        await self._conectar()
        self._keepalive = asyncio.create_task(self._keepalive_loop())

    async def _conectar(self) -> None:
        """Abre una conexión nueva y registra LISTEN + termination listener. Reemplaza la anterior."""
        viejo = self._conn
        conn = await asyncpg.connect(self._dsn)
        await conn.add_listener(CHANNEL, self._on_notify)
        conn.add_termination_listener(self._on_terminacion)
        self._conn = conn
        if viejo is not None and not viejo.is_closed():
            try:
                viejo.remove_termination_listener(self._on_terminacion)
            except Exception:  # noqa: BLE001 — limpieza best-effort de la conexión vieja
                pass
            try:
                await viejo.close()
            except Exception:  # noqa: BLE001
                pass
        log.info("listener_conectado", tenant_id=self._tenant_id, suscriptores=len(self._subscribers))

    def _on_notify(self, _conn, _pid, _channel, payload: str) -> None:
        """Reparte el NOTIFY a cada cola viva. Un error en una cola no rompe a las demás."""
        log.info("listener_evento", tenant_id=self._tenant_id, suscriptores=len(self._subscribers))
        for queue in tuple(self._subscribers):   # snapshot: robusto si el set cambia
            try:
                queue.put_nowait(payload)
            except Exception:  # noqa: BLE001 — cola llena/cerrada de un suscriptor: no frenar al resto
                log.warning("listener_cola_error", tenant_id=self._tenant_id)

    def _on_terminacion(self, _conn) -> None:
        """Callback síncrono de asyncpg al caerse la conexión: dispara la reconexión en una tarea."""
        if self._cerrado:
            return
        log.warning("listener_terminado", tenant_id=self._tenant_id)
        try:
            asyncio.create_task(self._reconectar())
        except RuntimeError:  # sin loop corriendo (apagado): nada que reconectar
            pass

    async def _reconectar(self) -> None:
        """Reabre la conexión preservando los suscriptores. Serializado e idempotente (backoff)."""
        async with self._lock:
            if self._cerrado:
                return
            if self._conn is not None and not self._conn.is_closed():
                return  # otra tarea ya reconectó
            intento = 0
            while not self._cerrado:
                try:
                    await self._conectar()
                    log.info("listener_reconectado", tenant_id=self._tenant_id, intento=intento)
                    return
                except Exception as exc:  # noqa: BLE001 — reintenta con backoff
                    intento += 1
                    espera = min(2.0 ** intento, _BACKOFF_MAX_S)
                    log.warning(
                        "listener_reconexion_fallida", tenant_id=self._tenant_id,
                        intento=intento, espera_s=espera, error=type(exc).__name__,
                    )
                    await asyncio.sleep(espera)

    async def _keepalive_loop(self) -> None:
        """Mantiene viva la conexión (SELECT 1) y detecta cuando murió sin avisar → reconecta."""
        while not self._cerrado:
            await asyncio.sleep(_KEEPALIVE_S)
            if self._cerrado:
                return
            conn = self._conn
            if conn is None or conn.is_closed():
                continue  # la reconexión ya está en curso
            try:
                await conn.fetchval("SELECT 1")
            except Exception:  # noqa: BLE001 — conexión muerta: ciérrala y fuerza reconexión
                log.warning("listener_keepalive_error", tenant_id=self._tenant_id)
                try:
                    conn.remove_termination_listener(self._on_terminacion)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001
                    pass
                asyncio.create_task(self._reconectar())

    def add(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.add(queue)

    def remove(self, queue: asyncio.Queue[str]) -> None:
        self._subscribers.discard(queue)

    @property
    def empty(self) -> bool:
        return not self._subscribers

    async def stop(self) -> None:
        self._cerrado = True
        if self._keepalive is not None:
            self._keepalive.cancel()
            try:
                await self._keepalive
            except asyncio.CancelledError:
                pass
            self._keepalive = None
        if self._conn is not None:
            try:
                await self._conn.remove_listener(CHANNEL, self._on_notify)
            except Exception:  # noqa: BLE001
                pass
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
                listener = _TenantListener(tenant_id, dsn)
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
