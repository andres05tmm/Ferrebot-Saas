"""THROWAWAY — diagnostica por qué pg_notify no llega al listener SSE en prod (no commitear el uso).

Reproduce el puente de eventos para el tenant `clinica-demo`:
  (a) Resuelve su `connection_url` por el MISMO camino que el hub (resolve_tenant_by_slug) e imprime
      host:port de esa URL y de la del control DB — SIN credenciales (6432 = PgBouncer, 5432 = directo).
  (b) Dos conexiones asyncpg DIRECTAS al mismo DSN: una hace LISTEN ferrebot_events, la otra
      pg_notify; reporta si el LISTEN recibió el NOTIFY (esto es lo que hace el hub).
  (c) Igual, pero publicando por la SESIÓN SQLAlchemy del tenant (camino REAL del publish) + commit.

Veredicto: distingue "PgBouncer rompe LISTEN" (6432 + (b) mudo) vs "publisher/listener en DB/host
distintos" vs "problema de commit". Logging estructurado con tenant_id; nunca imprime secretos.

Uso (en el servidor, por railway ssh):  python -m tools.diag_sse
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import asyncpg

from core.config import get_settings
from core.db.session import tenant_session, control_session
from core.db.urls import to_libpq
from core.events.publisher import CHANNEL, publish
from core.logging import configure_logging, get_logger
from core.tenancy.control_repo import resolve_tenant_by_slug

SLUG = "clinica-demo"
_ESPERA_S = 3.0
log = get_logger("diag.sse")


def _host_port(url: str) -> str:
    """host:port de un DSN, SIN usuario/clave (no se loguean secretos)."""
    p = urlsplit(url)
    return f"{p.hostname}:{p.port}"


async def main() -> int:
    configure_logging()
    settings = get_settings()

    async with control_session() as cs:
        tenant = await resolve_tenant_by_slug(cs, SLUG)
    if tenant is None:
        log.error("diag_tenant_no_encontrado", slug=SLUG)
        return 1

    puerto = urlsplit(tenant.connection_url).port
    # (a) Topología: a qué host:port pegan el tenant y el control DB.
    log.info(
        "diag_topologia",
        tenant_id=tenant.id,
        tenant_db=tenant.db_name,
        tenant_conn=_host_port(tenant.connection_url),     # lo que abre el hub (LISTEN)
        control_conn=_host_port(settings.control_database_url),
        puerto_tenant=puerto,
        pooler_probable=(puerto == 6432),
    )

    dsn = to_libpq(tenant.connection_url)   # forma libpq == la que pasa el hub a asyncpg.connect
    recibidos: list[str] = []

    def _on_notify(_conn, _pid, _channel, payload: str) -> None:
        recibidos.append(payload)

    # asyncpg sobre PgBouncer necesita statement_cache_size=0 (mismo workaround del control engine).
    conn_listen = await asyncpg.connect(dsn, statement_cache_size=0)
    b_ok = c_ok = False
    try:
        await conn_listen.add_listener(CHANNEL, _on_notify)

        # (b) publish por OTRA conexión asyncpg directa al MISMO DSN (literal, sin prepared stmt).
        try:
            conn_pub = await asyncpg.connect(dsn, statement_cache_size=0)
            try:
                await conn_pub.execute(f"SELECT pg_notify('{CHANNEL}', 'diag-asyncpg')")
            finally:
                await conn_pub.close()
            await asyncio.sleep(_ESPERA_S)
            b_ok = any("diag-asyncpg" in r for r in recibidos)
        except Exception as exc:  # noqa: BLE001 — un fallo aquí también es señal (lo reportamos)
            log.error("diag_b_error", error=type(exc).__name__, detalle=str(exc))
        log.info("diag_b_asyncpg_directo", entrego=b_ok)

        # (c) publish por la SESIÓN SQLAlchemy del tenant (camino REAL) + commit al cerrar el generador.
        n_antes = len(recibidos)
        try:
            async for s in tenant_session(tenant):
                await publish(s, "diag_evento", {"via": "sqlalchemy"})
            await asyncio.sleep(_ESPERA_S)
            c_ok = len(recibidos) > n_antes
        except Exception as exc:  # noqa: BLE001
            log.error("diag_c_error", error=type(exc).__name__, detalle=str(exc))
        log.info("diag_c_sqlalchemy_sesion", entrego=c_ok)
    finally:
        try:
            await conn_listen.remove_listener(CHANNEL, _on_notify)
        except Exception:  # noqa: BLE001
            pass
        await conn_listen.close()

    # --- Veredicto ----------------------------------------------------------
    if not b_ok and not c_ok:
        if puerto == 6432:
            veredicto = (
                "PgBouncer (6432) rompe LISTEN/NOTIFY: el listener SSE debe usar una URL DIRECTA a "
                "Postgres (5432), separada del pooler que usan las sesiones. El publish puede seguir "
                "por el pooler."
            )
        else:
            veredicto = (
                f"LISTEN mudo en conexiones directas al mismo DSN (puerto {puerto}). Si NO es un "
                "pooler, listener y publisher podrían caer en backends/bases distintas: revisar que "
                "connection_url apunte a la MISMA base por ambos lados."
            )
    elif b_ok and not c_ok:
        veredicto = (
            "asyncpg directo SÍ entrega, pero el publish por la sesión SQLAlchemy NO: revisar el "
            "commit/orden del publish o que la sesión del tenant pegue a otra base/host."
        )
    elif not b_ok and c_ok:
        veredicto = "Resultado raro (directo mudo, sesión sí): re-correr; posible carrera del LISTEN."
    else:
        veredicto = (
            "pg_notify llega por AMBOS caminos: el puente funciona. El bug está en el hub/SSE de prod "
            "(p. ej. el listener del tenant no está vivo, o usa otro DSN del que recibe el publish)."
        )
    log.info("diag_veredicto", b_directo=b_ok, c_sesion=c_ok, veredicto=veredicto)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
