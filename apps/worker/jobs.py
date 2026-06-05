"""Lógica testeable del job de emisión (separada del runtime ARQ de `apps.worker.main`).

`emitir_documento` consume la `Decision` de `service.emitir` (E4b-1) y la traduce a la semántica del
worker: reintentar (`Retry` con backoff), dead-letter o terminal. NUNCA propaga otra excepción
(`emitir` ya no lanza). El backoff es una función pura.

RED (E4b-2): `_backoff` y `emitir_documento` lanzan NotImplementedError; el shape es definitivo.
"""
from __future__ import annotations

from arq import Retry

from core.logging import get_logger

log = get_logger("worker.facturacion")


def _backoff(job_try: int, *, base: int = 30, tope: int = 3600) -> int:
    """Backoff exponencial acotado: `min(base * 2 ** (job_try - 1), tope)` segundos. PURO."""
    return min(base * 2 ** (job_try - 1), tope)


async def emitir_documento(ctx: dict, tenant_id: int, factura_id: int) -> str:
    """Emite la factura y traduce la `Decision` (E4b-1) a la semántica del worker ARQ.

    `servicio = await ctx["crear_servicio"](tenant_id)` (seam inyectado por `on_startup`);
    `decision = await servicio.emitir(factura_id)`. reintentar → `Retry` con backoff; dead_letter →
    log + "dead_letter"; si no → `decision.estado`. Nunca propaga otra excepción (`emitir` no lanza).
    """
    servicio = await ctx["crear_servicio"](tenant_id)
    decision = await servicio.emitir(factura_id)
    if decision.reintentar:
        raise Retry(defer=_backoff(ctx.get("job_try", 1)))
    if decision.dead_letter:
        log.warning("emision_dead_letter", tenant_id=tenant_id, factura_id=factura_id)
        return "dead_letter"
    return decision.estado
