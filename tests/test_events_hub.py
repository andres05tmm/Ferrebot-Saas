"""Hub de eventos en tiempo real (core/events/hub.py) contra Postgres real (fixture `tenant`).

Cubre lo que el unit con sesión compartida NO veía: (a) entrega REAL de pg_notify→listener en
CONEXIONES SEPARADAS (NOTIFY por otra conexión asyncpg y por la sesión SQLAlchemy), y (b) que el
listener se RECONECTA cuando su conexión se cae (se mata su backend desde otra conexión, como un idle
drop de la red) y vuelve a entregar a los suscriptores vivos.
"""
import asyncio
import itertools
import json

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.urls import to_libpq
from core.events.hub import event_hub
from core.events.publisher import CHANNEL, publish

# tenant_id ARBITRARIO por test (el hub solo lo usa como clave; el fixture `tenant` no trae id).
_ids = itertools.count(900_001)


def _tid() -> int:
    return next(_ids)


async def _esperar_evento(queue: asyncio.Queue[str]) -> dict:
    return json.loads(await asyncio.wait_for(queue.get(), timeout=5.0))


async def test_entrega_cross_conexion_asyncpg_y_sesion(tenant):
    tid = _tid()
    queue = await event_hub.subscribe(tid, tenant.url)
    try:
        # (1) NOTIFY por OTRA conexión asyncpg directa al mismo DSN.
        pub = await asyncpg.connect(to_libpq(tenant.url))
        try:
            await pub.execute(
                f"SELECT pg_notify('{CHANNEL}', $1)", json.dumps({"event": "directo", "data": {}})
            )
        finally:
            await pub.close()
        assert (await _esperar_evento(queue))["event"] == "directo"

        # (2) NOTIFY por la sesión SQLAlchemy del tenant (camino REAL del publish) + commit.
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            await publish(s, "sesion", {"a": 1})
            await s.commit()
        assert (await _esperar_evento(queue))["event"] == "sesion"
    finally:
        await event_hub.unsubscribe(tid, queue)


async def test_reconecta_tras_caida_y_sigue_entregando(tenant):
    tid = _tid()
    queue = await event_hub.subscribe(tid, tenant.url)
    try:
        listener = event_hub._listeners[tid]
        pid = listener._conn.get_server_pid()

        # Matar el backend del listener desde otra conexión: simula una caída real (idle TCP drop).
        admin = await asyncpg.connect(to_libpq(tenant.url))
        try:
            await admin.execute("SELECT pg_terminate_backend($1)", pid)
        finally:
            await admin.close()

        # El termination listener debe disparar la reconexión: nueva conexión con OTRO pid.
        for _ in range(100):
            c = listener._conn
            if c is not None and not c.is_closed() and c.get_server_pid() != pid:
                break
            await asyncio.sleep(0.05)
        assert listener._conn is not None and not listener._conn.is_closed()
        assert listener._conn.get_server_pid() != pid          # reconectó (backend distinto)

        # El suscriptor vivo (misma cola) sigue recibiendo tras la reconexión.
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            await publish(s, "post_reconnect", {})
            await s.commit()
        assert (await _esperar_evento(queue))["event"] == "post_reconnect"
    finally:
        await event_hub.unsubscribe(tid, queue)
