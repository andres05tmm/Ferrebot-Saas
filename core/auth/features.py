"""Gate de capacidades (feature flags) para los routers del API.

`feature-flags.md`: si la empresa no tiene la capacidad, la ruta responde **404** (como si no
existiera), no 403. `require_feature` es una dependency factory reutilizable; `get_capacidades`
resuelve las capacidades efectivas de la empresa del request (control DB) y los tests lo overridean.

RED (E4e): `verificar_feature` y `get_capacidades` lanzan NotImplementedError; `require_feature`
(la fábrica) es definitiva.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from core.db.session import control_session
from core.tenancy.capacidades import ControlCapacidades


def verificar_feature(feature: str, capacidades: frozenset[str]) -> None:
    """Lanza HTTPException(404) si `feature` no está en `capacidades` (feature-flags.md). PURO.

    Mensaje genérico: no revelar que la feature existe pero está deshabilitada (como si no existiera).
    """
    if feature not in capacidades:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Recurso no disponible")


async def get_capacidades(request: Request) -> frozenset[str]:
    """Capacidades efectivas de la empresa del request (control DB, sesión per-call)."""
    async with control_session() as cs:
        return await ControlCapacidades(cs).efectivas(request.state.tenant.id)


def require_feature(feature: str):
    """Dependency factory: verifica `feature` contra `get_capacidades` (404 si falta)."""

    async def _dep(capacidades: frozenset[str] = Depends(get_capacidades)) -> None:
        verificar_feature(feature, capacidades)

    return _dep
