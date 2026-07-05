"""Prompt-priming de Whisper con el vocabulario del catálogo del tenant.

Whisper acepta un `prompt` que sesga la transcripción hacia ese vocabulario: pasarle los productos
más vendidos del negocio reduce los errores en nombres propios de ferretería ("wayper", "varsol",
"pegaternit", "drywall") que el modelo genérico transcribe mal, y por tanto los re-preguntas del
agente (menos turnos = menos tokens).

El vocabulario es DATA estable durante el día: se cachea por tenant en proceso con TTL de 1h (el bot
corre un proceso por empresa; recomputar por turno sería una consulta extra sin ganancia). El límite
de ~40 términos mantiene el prompt corto (Whisper solo mira los últimos 224 tokens del prompt).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.logging import get_logger

log = get_logger("voz.priming")

_LIMITE_TERMINOS = 40
_TTL_SEGUNDOS = 3600
_PREAMBULO = "Venta en una ferretería colombiana. Productos:"

# Consulta: los más VENDIDOS primero (lo que más se dicta por voz); si no hay ventas aún, los activos.
_SQL_TOP_VENDIDOS = text(
    "SELECT p.nombre FROM ventas_detalle d JOIN productos p ON p.id = d.producto_id "
    "WHERE p.activo GROUP BY p.nombre ORDER BY count(*) DESC, p.nombre LIMIT :limite"
)
_SQL_ACTIVOS = text("SELECT nombre FROM productos WHERE activo ORDER BY id LIMIT :limite")


@dataclass(slots=True)
class _Entrada:
    prompt: str
    expira_epoch: float


_CACHE: dict[int, _Entrada] = {}


def formatear_prompt(terminos: list[str]) -> str | None:
    """Vocabulario → prompt de Whisper (puro). None si no hay términos (no sesgar de más)."""
    limpios = [t.strip() for t in terminos if t and t.strip()]
    if not limpios:
        return None
    return f"{_PREAMBULO} {', '.join(limpios)}."


async def _leer_terminos(session: AsyncSession) -> list[str]:
    filas = (await session.execute(_SQL_TOP_VENDIDOS, {"limite": _LIMITE_TERMINOS})).scalars().all()
    if not filas:
        filas = (await session.execute(_SQL_ACTIVOS, {"limite": _LIMITE_TERMINOS})).scalars().all()
    return list(filas)


async def prompt_para_tenant(session: AsyncSession, tenant_id: int) -> str | None:
    """Prompt de priming del tenant (cacheado 1h). None si el catálogo está vacío o si algo falla
    (best-effort: la voz nunca debe romperse por el priming)."""
    ahora = now_co().timestamp()
    entrada = _CACHE.get(tenant_id)
    if entrada is not None and entrada.expira_epoch > ahora:
        return entrada.prompt or None
    try:
        prompt = formatear_prompt(await _leer_terminos(session))
    except Exception:
        log.warning("voz_priming_fallo", tenant_id=tenant_id, exc_info=True)
        return None
    _CACHE[tenant_id] = _Entrada(prompt=prompt or "", expira_epoch=ahora + _TTL_SEGUNDOS)
    return prompt


def limpiar_cache() -> None:
    """Para tests: vacía la caché en proceso."""
    _CACHE.clear()
