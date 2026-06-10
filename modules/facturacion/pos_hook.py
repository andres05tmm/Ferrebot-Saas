"""Hook post-venta del POS electrónico (ADR 0012 D2): cierra la venta de mostrador con un documento POS.

Lo invoca el router de ventas tras registrar la venta (mismo `session` del tenant → el pendiente POS se
commitea junto con la venta). Defensivo y barato cuando el POS está apagado: si no hay tenant resuelto,
ni pool ARQ, o la empresa no tiene la capacidad `pos_electronico`, no hace nada (la inmensa mayoría de
las ventas). Encola la emisión SOLO cuando crea un pendiente nuevo (idempotencia del cierre, D2).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from core.db.session import control_session
from core.logging import get_logger
from core.tenancy.capacidades import ControlCapacidades
from modules.facturacion.repository import SqlFacturacionRepository
from modules.facturacion.service import FacturacionService

log = get_logger("facturacion.pos_hook")

FEATURE_POS = "pos_electronico"
JOB_EMITIR = "emitir_documento"


async def _encolar_si_aplica(
    *, servicio: FacturacionService, capacidades: frozenset[str], tenant_id: int,
    enqueue: Callable[..., Awaitable[object]], venta_id: int,
) -> int | None:
    """Núcleo testeable: si la empresa tiene POS y se CREA un pendiente nuevo, encola la emisión.

    Devuelve el `factura_id` encolado, o None (sin capacidad, excluido por FE/POS existente, o pendiente
    ya creado → no se re-encola, evitando una segunda emisión y un segundo documento DIAN)."""
    if FEATURE_POS not in capacidades:
        return None
    factura, creada = await servicio.crear_pendiente_pos(venta_id)
    if creada and factura is not None:
        await enqueue(JOB_EMITIR, tenant_id, factura.id)
        return factura.id
    return None


async def encolar_cierre_pos(request: Request, session: AsyncSession, venta_id: int) -> None:
    """Resuelve tenant/capacidades/pool y delega en `_encolar_si_aplica`. No lanza nunca.

    Un fallo del cierre POS no debe tumbar el registro de la venta (la venta es la operación crítica; el
    documento fiscal va desacoplado, como toda la emisión)."""
    tenant = getattr(request.state, "tenant", None)
    arq_pool = getattr(getattr(request.app, "state", None), "arq_pool", None)
    if tenant is None or arq_pool is None:
        return
    try:
        async with control_session() as cs:
            capacidades = await ControlCapacidades(cs).efectivas(tenant.id)
        factura_id = await _encolar_si_aplica(
            servicio=FacturacionService(SqlFacturacionRepository(session)),
            capacidades=capacidades, tenant_id=tenant.id,
            enqueue=arq_pool.enqueue_job, venta_id=venta_id,
        )
        if factura_id is not None:
            log.info("pos_cierre_encolado", tenant_id=tenant.id, venta_id=venta_id, factura_id=factura_id)
    except Exception:  # noqa: BLE001 — el cierre POS jamás rompe el registro de la venta
        log.warning("pos_cierre_fallo", venta_id=venta_id, exc_info=True)
