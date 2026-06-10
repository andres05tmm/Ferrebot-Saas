"""Cierre fiscal de mostrador con POS electrónico (ADR 0012 D2): un único núcleo, dos cableados.

El cierre se invoca en el **punto común post-registro de la venta** — el router HTTP (`/ventas`) y el
handler `_registrar_venta` del agente (convergencia de bypass/confirmación/modelo, canal principal del
mostrador). Contrato innegociable: **jamás rompe la venta** (un fallo del cierre se traga y loguea),
idempotente (`pos:{venta_id}`) y excluyente con la FE (D1).

Carrera commit↔encolado (fix de auditoría): el núcleo **commitea el pendiente ANTES de encolar**
`emitir_documento`. Si se encolara antes del commit, el worker podría correr `emitir()` sin que la fila
exista todavía (caería al reconciliador en vez del camino feliz). Commit-antes-de-encolar lo elimina.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from core.config import get_settings
from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.capacidades import ControlCapacidades
from modules.facturacion.repository import SqlFacturacionRepository
from modules.facturacion.service import FacturacionService

log = get_logger("facturacion.pos_hook")

FEATURE_POS = "pos_electronico"
JOB_EMITIR = "emitir_documento"

Enqueue = Callable[..., Awaitable[Any]]


async def cerrar_venta_con_pos(
    *, servicio: FacturacionService, session: AsyncSession, venta_id: int,
    tenant_id: int, capacidades: frozenset[str], enqueue: Enqueue,
) -> int | None:
    """Núcleo del cierre POS. Crea el pendiente, **commitea** y luego encola. Devuelve `factura_id` o None.

    None = POS apagado / excluido por FE-POS existente / pendiente ya creado (no se re-encola: evita una
    segunda emisión y un segundo documento DIAN). El `commit` ocurre SOLO cuando se crea un pendiente
    nuevo, así que con el POS apagado no altera la transacción de la venta."""
    if FEATURE_POS not in capacidades:
        return None
    factura, creada = await servicio.crear_pendiente_pos(venta_id)
    if not (creada and factura is not None):
        return None
    await session.commit()                       # commit ANTES de encolar: el worker ve la fila (sin carrera)
    await enqueue(JOB_EMITIR, tenant_id, factura.id)
    return factura.id


def _servicio(session: AsyncSession) -> FacturacionService:
    return FacturacionService(SqlFacturacionRepository(session))


# ── Enqueue perezoso para caminos sin pool ARQ inyectado (bot Telegram) ───────
_pool: Any = None
_pool_lock = asyncio.Lock()


async def _enqueue_lazy(job: str, *args: Any) -> None:
    """Encola en ARQ con un pool MEMOIZADO por proceso (creado del `redis_url`). Perezoso: no toca red al
    importar. Lo usa el cierre del bot, que no recibe el pool del lifespan del API."""
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                from arq import create_pool
                from arq.connections import RedisSettings
                _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    await _pool.enqueue_job(job, *args)


class CierrePos:
    """Puerto de cierre POS para el agente (inyectado en `ai.tools.Deps`, cableado en `apps.bot.wiring`).

    Atado a la sesión del tenant del turno; las capacidades llegan del `Contexto` (ya resueltas/cacheadas),
    no se vuelve a pegar al control DB. Nunca lanza: el cierre fiscal jamás tumba el registro de la venta."""

    def __init__(self, session: AsyncSession, *, enqueue: Enqueue | None = None) -> None:
        self._session = session
        self._enqueue = enqueue or _enqueue_lazy

    async def cerrar(self, venta_id: int, *, tenant_id: int, capacidades: frozenset[str]) -> None:
        try:
            await cerrar_venta_con_pos(
                servicio=_servicio(self._session), session=self._session, venta_id=venta_id,
                tenant_id=tenant_id, capacidades=capacidades, enqueue=self._enqueue,
            )
        except Exception:  # noqa: BLE001 — el cierre POS jamás rompe el registro de la venta
            log.warning("pos_cierre_fallo", venta_id=venta_id, exc_info=True)


async def encolar_cierre_pos(request: Request, session: AsyncSession, venta_id: int) -> None:
    """Cableado HTTP del cierre (router `/ventas`): capacidades del control DB + pool ARQ del lifespan.

    Resuelve tenant/pool del `request`; si faltan (apps mínimas de test) no hace nada. Nunca lanza."""
    tenant = getattr(request.state, "tenant", None)
    arq_pool = getattr(getattr(request.app, "state", None), "arq_pool", None)
    if tenant is None or arq_pool is None:
        return
    try:
        async with control_session() as cs:
            capacidades = await ControlCapacidades(cs).efectivas(tenant.id)
        factura_id = await cerrar_venta_con_pos(
            servicio=_servicio(session), session=session, venta_id=venta_id,
            tenant_id=tenant.id, capacidades=capacidades, enqueue=arq_pool.enqueue_job,
        )
        if factura_id is not None:
            log.info("pos_cierre_encolado", tenant_id=tenant.id, venta_id=venta_id, factura_id=factura_id)
    except Exception:  # noqa: BLE001 — el cierre POS jamás rompe el registro de la venta
        log.warning("pos_cierre_fallo", venta_id=venta_id, exc_info=True)
