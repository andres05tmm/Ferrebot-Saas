"""Gobierno de agentes (ADR 0024): rate-limit + presupuesto diario, con contadores atómicos en Redis.

Invariantes críticos (test-primero):
  - Presupuesto excedido → el turno se CORTA (nunca llega a llamar al modelo). Aquí se verifica al nivel
    del gobierno (Decision.permitido=False); el "0 llamadas al provider" se prueba en la integración del
    turno (test_turno_gobierno / test_wa_agent).
  - Contador de presupuesto ATÓMICO bajo concurrencia (`asyncio.gather` de N reservas → sin sobregiro).
  - AISLAMIENTO por tenant: el rate-limit/presupuesto de la empresa A jamás afecta ni lee el de la B.

La lógica del orquestador se prueba con un `FakeGobiernoStore` (determinista, sin red); la atomicidad
real se prueba contra el Redis del entorno (Docker) con claves de tenant aleatorias para no colisionar
con otras suites en paralelo, y limpiando las claves al final.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from core.config import get_settings
from core.llm.gobierno import (
    MENSAJE_PRESUPUESTO,
    MENSAJE_RATE,
    Corte,
    Gobierno,
    PoliticaGobierno,
    RedisGobierno,
)


# --------------------------------- fakes ----------------------------------
class FakeGobiernoStore:
    """Compuertas en memoria con la MISMA semántica atómica que el Lua (sin await interno = atómico)."""

    def __init__(self) -> None:
        self.rate: dict[tuple[int, int], int] = {}
        self.budget: dict[tuple[int, str], int] = {}

    async def permitir_rate(self, tenant_id, usuario_id, limite, ventana_s) -> bool:
        k = (tenant_id, usuario_id)
        self.rate[k] = self.rate.get(k, 0) + 1
        return self.rate[k] <= limite

    async def reservar_presupuesto(self, tenant_id, fecha, costo, limite, ttl_s) -> bool:
        k = (tenant_id, fecha)
        usado = self.budget.get(k, 0)
        if usado + costo > limite:
            return False
        self.budget[k] = usado + costo
        return True


class FakeConfigStore:
    def __init__(self, overrides: dict[int, dict[str, str]]) -> None:
        self._o = overrides

    async def overrides(self, empresa_id: int) -> dict[str, str]:
        return self._o.get(empresa_id, {})


# --------------------------- orquestación (fakes) --------------------------
async def test_kill_switch_apagado_siempre_permite():
    gob = Gobierno(
        store=FakeGobiernoStore(),
        plataforma=PoliticaGobierno(habilitado=False, rate_limite=1, presupuesto_diario=1),
    )
    d1 = await gob.evaluar(1, 1)
    d2 = await gob.evaluar(1, 1)
    assert d1.permitido and d2.permitido      # ni rate ni presupuesto operan


async def test_limites_en_cero_no_operan():
    gob = Gobierno(store=FakeGobiernoStore(), plataforma=PoliticaGobierno())  # 0/0 por defecto
    for _ in range(5):
        assert (await gob.evaluar(1, 1)).permitido


async def test_rate_limit_corta_tras_el_tope_con_mensaje():
    gob = Gobierno(store=FakeGobiernoStore(), plataforma=PoliticaGobierno(rate_limite=2))
    assert (await gob.evaluar(1, 9)).permitido
    assert (await gob.evaluar(1, 9)).permitido
    d = await gob.evaluar(1, 9)
    assert d.permitido is False
    assert d.corte is Corte.RATE
    assert d.mensaje == MENSAJE_RATE


async def test_presupuesto_excedido_corta_y_no_permite():
    # presupuesto 3000, costo estimado 1500 → exactamente 2 turnos, el 3º se corta.
    gob = Gobierno(
        store=FakeGobiernoStore(),
        plataforma=PoliticaGobierno(presupuesto_diario=3000, costo_estimado_turno=1500),
    )
    assert (await gob.evaluar(1, 1)).permitido
    assert (await gob.evaluar(1, 1)).permitido
    d = await gob.evaluar(1, 1)
    assert d.permitido is False
    assert d.corte is Corte.PRESUPUESTO
    assert d.mensaje == MENSAJE_PRESUPUESTO


async def test_rate_se_evalua_antes_del_presupuesto():
    # Un corte por rate NO debe consumir presupuesto (el contador de budget queda intacto).
    store = FakeGobiernoStore()
    gob = Gobierno(
        store=store,
        plataforma=PoliticaGobierno(
            rate_limite=1, presupuesto_diario=100000, costo_estimado_turno=1500
        ),
    )
    await gob.evaluar(1, 1)           # permite (consume 1 de rate + 1500 de budget)
    d = await gob.evaluar(1, 1)       # cortado por rate
    assert d.corte is Corte.RATE
    assert store.budget[(1, list(store.budget)[0][1])] == 1500  # solo la 1ª reservó presupuesto


async def test_override_por_empresa_activa_presupuesto():
    # Plataforma sin presupuesto; la empresa 7 lo activa por config_empresa.
    gob = Gobierno(
        store=FakeGobiernoStore(),
        plataforma=PoliticaGobierno(costo_estimado_turno=1000),
        config_store=FakeConfigStore({7: {"llm_presupuesto_diario": "1000"}}),
    )
    assert (await gob.evaluar(7, 1)).permitido        # 1er turno cabe (1000<=1000)
    d = await gob.evaluar(7, 1)                        # 2º excede
    assert d.corte is Corte.PRESUPUESTO
    # La empresa 1 (sin override) no tiene presupuesto: nunca se corta.
    for _ in range(5):
        assert (await gob.evaluar(1, 1)).permitido


async def test_aislamiento_por_tenant_en_el_orquestador():
    # El presupuesto de la empresa A agotado no afecta a la B (claves por tenant en el store).
    gob = Gobierno(
        store=FakeGobiernoStore(),
        plataforma=PoliticaGobierno(presupuesto_diario=1500, costo_estimado_turno=1500),
    )
    assert (await gob.evaluar(100, 1)).permitido       # A agota su presupuesto
    assert (await gob.evaluar(100, 1)).permitido is False
    assert (await gob.evaluar(200, 1)).permitido       # B intacto


# ----------------------- atomicidad real contra Redis ----------------------
def _redis_o_skip():
    import redis.asyncio as redis

    return redis.from_url(get_settings().redis_url, decode_responses=True)


@pytest.mark.asyncio
async def test_presupuesto_atomico_bajo_concurrencia():
    """N reservas concurrentes contra un presupuesto de N/2 costos → exactamente N/2 permitidas."""
    cliente = _redis_o_skip()
    tenant = uuid.uuid4().int % 10_000_000 + 90_000_000   # id aleatorio, sin colisión con otras suites
    fecha = "2026-07-03"
    costo, permitidos = 1000, 8
    limite = costo * permitidos
    store = RedisGobierno(url="", client=cliente)
    key = f"llm:budget:{tenant}:{fecha}"
    try:
        await cliente.delete(key)
        resultados = await asyncio.gather(
            *[store.reservar_presupuesto(tenant, fecha, costo, limite, 60) for _ in range(permitidos * 3)]
        )
        assert sum(1 for r in resultados if r) == permitidos       # ni uno de más
        assert int(await cliente.get(key)) == limite               # contador exacto, sin sobregiro
    finally:
        await cliente.delete(key)
        await cliente.aclose()


@pytest.mark.asyncio
async def test_rate_limit_real_permite_solo_el_tope_en_la_ventana():
    cliente = _redis_o_skip()
    tenant = uuid.uuid4().int % 10_000_000 + 90_000_000
    store = RedisGobierno(url="", client=cliente)
    key = f"llm:rl:{tenant}:5"
    try:
        await cliente.delete(key)
        resultados = [await store.permitir_rate(tenant, 5, 3, 60) for _ in range(6)]
        assert resultados == [True, True, True, False, False, False]
    finally:
        await cliente.delete(key)
        await cliente.aclose()


@pytest.mark.asyncio
async def test_aislamiento_real_entre_tenants_en_redis():
    cliente = _redis_o_skip()
    a = uuid.uuid4().int % 10_000_000 + 90_000_000
    b = a + 1
    fecha = "2026-07-03"
    store = RedisGobierno(url="", client=cliente)
    ka, kb = f"llm:budget:{a}:{fecha}", f"llm:budget:{b}:{fecha}"
    try:
        await cliente.delete(ka, kb)
        assert await store.reservar_presupuesto(a, fecha, 1000, 1000, 60) is True   # A agota
        assert await store.reservar_presupuesto(a, fecha, 1000, 1000, 60) is False  # A sin cupo
        assert await store.reservar_presupuesto(b, fecha, 1000, 1000, 60) is True   # B intacto
        assert await cliente.get(kb) == "1000"
    finally:
        await cliente.delete(ka, kb)
        await cliente.aclose()
