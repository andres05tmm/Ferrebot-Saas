"""Gate de capacidades (feature flags) para los routers del API.

`feature-flags.md`: si la empresa no tiene la capacidad, la ruta responde **404** (como si no
existiera), no 403. `require_feature` es una dependency factory reutilizable; `get_capacidades`
resuelve las capacidades efectivas de la empresa del request (control DB) y los tests lo overridean.

RED (E4e): `verificar_feature` y `get_capacidades` lanzan NotImplementedError; `require_feature`
(la fábrica) es definitiva.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, status

from core.db.session import control_session
from core.tenancy.capacidades import ControlCapacidades
from core.tenancy.capacidades_cache import CapacidadesCache, capacidades_cache
from core.tenancy.catalogo import expandir_metapacks


def verificar_feature(feature: str, capacidades: frozenset[str]) -> None:
    """Lanza HTTPException(404) si `feature` no está en `capacidades` (feature-flags.md). PURO.

    Expande los meta-packs fail-safe (ADR 0021): `pos` satisface las finas aunque el llamador pase
    un set sin expandir (p. ej. overrides de test). Mensaje genérico: no revelar que la feature
    existe pero está deshabilitada (como si no existiera).
    """
    if feature not in expandir_metapacks(capacidades):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recurso no disponible")


async def _cargar_efectivas(empresa_id: int) -> frozenset[str]:
    """Carga las capacidades efectivas desde el control DB (sesión per-call). IO."""
    async with control_session() as cs:
        return await ControlCapacidades(cs).efectivas(empresa_id)


async def _resolver_capacidades(
    empresa_id: int,
    cache: CapacidadesCache,
    loader: Callable[[int], Awaitable[frozenset[str]]],
) -> frozenset[str]:
    """Capacidades de la empresa desde `cache`; en miss carga con `loader`, cachea y devuelve.

    Núcleo inyectable (cache + loader) para testear sin tocar el control DB real.
    """
    cacheadas = cache.get(empresa_id)
    if cacheadas is not None:
        return cacheadas
    efectivas = await loader(empresa_id)
    cache.set(empresa_id, efectivas)
    return efectivas


async def get_capacidades(request: Request) -> frozenset[str]:
    """Capacidades efectivas de la empresa del request, cacheadas con TTL corto (control DB en miss)."""
    return await _resolver_capacidades(request.state.tenant.id, capacidades_cache, _cargar_efectivas)


def require_feature(feature: str):
    """Dependency factory: verifica `feature` contra `get_capacidades` (404 si falta)."""

    async def _dep(capacidades: frozenset[str] = Depends(get_capacidades)) -> None:
        verificar_feature(feature, capacidades)

    return _dep
