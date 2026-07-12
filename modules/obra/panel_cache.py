"""Caché TTL del panel/home de obra (Fase 8) por empresa.

El panel agrega el gasto real de TODAS las obras vivas: barato con las consultas batcheadas del repo, pero
igual toca la BD del tenant. Un TTL corto lo sirve al instante en las recargas del dashboard (el objetivo
"<2s" del plan) sin pegarle a Postgres en cada abrir/refrescar de la pestaña.

Espeja el patrón de `core/tenancy/capacidades_cache.py` (mismo TTL en memoria, misma API get/set/invalidate).
Clave = `empresa_id`. Nota multi-instancia: es caché EN PROCESO (como `ControlCache`/`CapacidadesCache`
del repo), así que dos réplicas pueden servir hasta `TTL` de desfase — aceptable para un overview de solo
lectura; el paso a Redis (regla de performance) queda como evolución si se necesita coherencia estricta.
"""
import time
from typing import TypeVar

_TTL_SECONDS = 30.0

_T = TypeVar("_T")


class PanelCache:
    def __init__(self, ttl: float = _TTL_SECONDS) -> None:
        self._ttl = ttl
        self._data: dict[int, tuple[float, object]] = {}

    def get(self, empresa_id: int) -> object | None:
        entry = self._data.get(empresa_id)
        if entry is None:
            return None
        expires_at, valor = entry
        if time.monotonic() >= expires_at:
            self._data.pop(empresa_id, None)
            return None
        return valor

    def set(self, empresa_id: int, valor: object) -> None:
        self._data[empresa_id] = (time.monotonic() + self._ttl, valor)

    def invalidate(self, empresa_id: int) -> None:
        self._data.pop(empresa_id, None)

    def clear(self) -> None:
        """Vacía la caché entera (aísla estado global entre pruebas)."""
        self._data.clear()


panel_cache = PanelCache()

# Caché del cockpit de construcción (GET /obras/dashboard). TTL 30s, igual al panel: ninguna mutación
# invalida estas cachés y el frontend refetchea por SSE al instante — con 5 min el dueño imputaba un
# gasto y veía el cockpit "congelado" contradiciendo las listas vivas. 30s sigue absorbiendo las
# recargas rápidas (objetivo "<2s", spec 13); la invalidación exacta por mutación queda como evolución.
# ponytail: TTL corto en vez de invalidar en cada mutación; si 30s de desfase molesta, invalidar por empresa_id.
dashboard_cache = PanelCache(ttl=30.0)
