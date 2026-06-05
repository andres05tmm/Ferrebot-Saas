"""Caché de capacidades efectivas por empresa con TTL corto (espeja ControlCache).

Cubre el comportamiento de `CapacidadesCache` (get/set/invalidate con TTL) y que `get_capacidades`
—vía su núcleo inyectable `_resolver_capacidades`— carga del control DB una sola vez por empresa
dentro del TTL (loader contador en vez de DB real).
"""
from core.auth.features import _resolver_capacidades
from core.tenancy.capacidades_cache import CapacidadesCache


def test_get_dentro_del_ttl():
    cache = CapacidadesCache(ttl=60.0)
    caps = frozenset({"ventas", "fiados"})
    cache.set(1, caps)
    assert cache.get(1) == caps


def test_get_tras_expirar_ttl_es_miss():
    cache = CapacidadesCache(ttl=0.0)   # expira de inmediato (monotonic() >= expires_at)
    cache.set(1, frozenset({"ventas"}))
    assert cache.get(1) is None


def test_invalidate_es_miss():
    cache = CapacidadesCache()
    cache.set(7, frozenset({"ventas"}))
    cache.invalidate(7)
    assert cache.get(7) is None


async def test_resolver_carga_una_vez_por_empresa():
    cache = CapacidadesCache()
    llamadas: list[int] = []

    async def loader(empresa_id: int) -> frozenset[str]:
        llamadas.append(empresa_id)
        return frozenset({f"f{empresa_id}"})

    primero = await _resolver_capacidades(1, cache, loader)
    segundo = await _resolver_capacidades(1, cache, loader)

    assert primero == segundo == frozenset({"f1"})
    assert llamadas == [1]               # el control DB se consultó UNA sola vez


async def test_resolver_empresas_distintas_no_comparten():
    cache = CapacidadesCache()
    llamadas: list[int] = []

    async def loader(empresa_id: int) -> frozenset[str]:
        llamadas.append(empresa_id)
        return frozenset({f"f{empresa_id}"})

    a = await _resolver_capacidades(1, cache, loader)
    b = await _resolver_capacidades(2, cache, loader)

    assert a == frozenset({"f1"})
    assert b == frozenset({"f2"})
    assert sorted(llamadas) == [1, 2]
