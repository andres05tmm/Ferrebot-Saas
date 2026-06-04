"""Caché del control plane: slug -> ResolvedTenant con TTL corto (tenancy.md §3).

Evita pegarle al control DB en cada request. Invalidable al cambiar estado/branding.
"""
import time

from core.tenancy.context import ResolvedTenant

_TTL_SECONDS = 60.0


class ControlCache:
    def __init__(self, ttl: float = _TTL_SECONDS) -> None:
        self._ttl = ttl
        self._data: dict[str, tuple[float, ResolvedTenant]] = {}

    def get(self, slug: str) -> ResolvedTenant | None:
        entry = self._data.get(slug)
        if entry is None:
            return None
        expires_at, tenant = entry
        if time.monotonic() >= expires_at:
            self._data.pop(slug, None)
            return None
        return tenant

    def set(self, tenant: ResolvedTenant) -> None:
        self._data[tenant.slug] = (time.monotonic() + self._ttl, tenant)

    def invalidate(self, slug: str) -> None:
        self._data.pop(slug, None)


control_cache = ControlCache()
