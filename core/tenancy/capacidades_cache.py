"""Caché de capacidades efectivas por empresa con TTL corto (tenancy.md §3).

Espeja `ControlCache` (slug→ResolvedTenant): evita pegarle al control DB en cada request gateado.
Clave = `empresa_id`, valor = capacidades efectivas. Solo TTL por ahora; `invalidate` queda listo
para la invalidación explícita del toggle de admin (Fase 13).
"""
import time

_TTL_SECONDS = 60.0


class CapacidadesCache:
    def __init__(self, ttl: float = _TTL_SECONDS) -> None:
        self._ttl = ttl
        self._data: dict[int, tuple[float, frozenset[str]]] = {}

    def get(self, empresa_id: int) -> frozenset[str] | None:
        entry = self._data.get(empresa_id)
        if entry is None:
            return None
        expires_at, capacidades = entry
        if time.monotonic() >= expires_at:
            self._data.pop(empresa_id, None)
            return None
        return capacidades

    def set(self, empresa_id: int, capacidades: frozenset[str]) -> None:
        self._data[empresa_id] = (time.monotonic() + self._ttl, capacidades)

    def invalidate(self, empresa_id: int) -> None:
        self._data.pop(empresa_id, None)

    def clear(self) -> None:
        """Vacía toda la caché (útil para aislar estado global entre pruebas)."""
        self._data.clear()


capacidades_cache = CapacidadesCache()
