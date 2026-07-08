"""Análisis de precios de proveedor (Fase 8, spec 10) + los LOW de compras del review de Fase 3:

  - (b) alerta/promedio de precio de proveedor PONDERADO por cantidad (no promedio simple de líneas);
  - (c) `_mismo_payload` de la idempotencia compara TAMBIÉN `obra_id`/`es_viaje_material` (reusar una key
        cambiando la imputación es un conflicto, no un replay).

Integración real contra Postgres efímero + el módulo obra registrado para que la FK compras.obra_id resuelva.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.compras.errors import IdempotenciaConflicto
from modules.compras.repository import SqlComprasRepository
from modules.compras.schemas import CompraCrear
from modules.compras.service import ComprasService

import modules.obra.models  # noqa: E402,F401  (registra `obras` para la FK compras.obra_id)


async def _seed_usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Quien', 'admin') RETURNING id"))
    ).scalar_one()


async def _seed_obra(s: AsyncSession, *, nombre: str) -> int:
    cid = (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id"))
    ).scalar_one()
    return (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, :n) RETURNING id"),
            {"c": cid, "n": nombre},
        )
    ).scalar_one()


def _viaje(costo: str, cantidad: str = "1", *, key: str | None = None) -> CompraCrear:
    return CompraCrear(
        proveedor={"nombre": "Planta Única"}, categoria="MEZCLA_ASFALTICA",
        es_viaje_material=True, precio_venta_cliente=Decimal("999999"),
        items=[{"cantidad": Decimal(cantidad), "costo": Decimal(costo)}],
        idempotency_key=key,
    )


# ---- LOW (b): promedio de precio PONDERADO por cantidad ---------------------
async def test_alerta_precio_usa_promedio_ponderado_no_simple(tenant):
    """Compra chica cara + compra grande barata: el promedio SIMPLE (150) no alertaría a 130, pero el
    PONDERADO por cantidad (~101) sí. La alerta debe dispararse → prueba que el promedio se pondera."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _seed_usuario(s)
        await s.commit()
        svc = ComprasService(SqlComprasRepository(s))
        # Histórico: 100 uds a 100 (barato, mucho volumen) + 1 ud a 200 (caro, poco volumen).
        await svc.registrar(_viaje("100", "100"), usuario_id=uid)
        await svc.registrar(_viaje("200", "1"), usuario_id=uid)
        await s.commit()
        # Nueva compra a 130/u: > promedio ponderado (~101)·1.15=116 → alerta; < promedio simple (150)·1.15.
        r = await svc.registrar(_viaje("130", "1"), usuario_id=uid)
        await s.commit()
    assert r.compra.alerta_precio_proveedor is True


# ---- Análisis de precios de proveedor (Fase 8) ------------------------------
async def test_analisis_precios_agrega_ponderado_min_max_y_alerta(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _seed_usuario(s)
        await s.commit()
        svc = ComprasService(SqlComprasRepository(s))
        await svc.registrar(_viaje("100", "100"), usuario_id=uid)   # barato, volumen
        await svc.registrar(_viaje("200", "1"), usuario_id=uid)     # caro, poco volumen
        await s.commit()
        filas = await svc.analisis_precios(desde=None, hasta=None)

    assert len(filas) == 1
    fila = filas[0]
    assert fila.proveedor_nombre == "Planta Única"
    assert fila.categoria == "MEZCLA_ASFALTICA"
    assert fila.n_compras == 2
    assert fila.cantidad_total == Decimal("101")
    # Ponderado = (100·100 + 1·200) / 101 = 10200/101 = 100.99 (NO el simple (100+200)/2 = 150).
    assert fila.costo_unitario_promedio == Decimal("100.99")
    assert fila.costo_unitario_min == Decimal("100")
    assert fila.costo_unitario_max == Decimal("200")
    # El costo máximo (200) supera en >15% el promedio ponderado (100.99) → alerta de sobreprecio.
    assert fila.alerta is True
    assert fila.variacion_pct > Decimal("90")


async def test_analisis_precios_vacio_sin_compras(tenant):
    async with AsyncSession(tenant.engine) as s:
        filas = await ComprasService(SqlComprasRepository(s)).analisis_precios(desde=None, hasta=None)
    assert filas == []


# ---- LOW (c): `_mismo_payload` compara obra_id / es_viaje_material ----------
async def test_misma_key_distinta_imputacion_es_conflicto(tenant):
    """Reusar la idempotency_key con las MISMAS líneas/total pero distinta imputación (viaje → imputada a
    obra) NO es un replay: es un conflicto (antes el guard ignoraba obra_id/es_viaje_material)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _seed_usuario(s)
        obra_id = await _seed_obra(s, nombre="Vía")
        await s.commit()
        svc = ComprasService(SqlComprasRepository(s))
        # (1) Viaje de material (sin obra), key K.
        await svc.registrar(_viaje("1000", "1", key="K1"), usuario_id=uid)
        await s.commit()
        # (2) Misma key, mismas líneas/total, pero AHORA imputada a una obra (no viaje) → conflicto.
        conflicto = CompraCrear(
            proveedor={"nombre": "Planta Única"}, categoria="MEZCLA_ASFALTICA",
            obra_id=obra_id, es_viaje_material=False,
            items=[{"cantidad": Decimal("1"), "costo": Decimal("1000")}],
            idempotency_key="K1",
        )
        try:
            await svc.registrar(conflicto, usuario_id=uid)
            asalto = False
        except IdempotenciaConflicto:
            asalto = True
    assert asalto is True


async def test_misma_key_mismo_payload_sigue_siendo_replay(tenant):
    """Regresión: con TODO igual (incluida la imputación), la misma key sigue siendo un replay inocuo."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _seed_usuario(s)
        await s.commit()
        svc = ComprasService(SqlComprasRepository(s))
        r1 = await svc.registrar(_viaje("1000", "1", key="K2"), usuario_id=uid)
        await s.commit()
        r2 = await svc.registrar(_viaje("1000", "1", key="K2"), usuario_id=uid)
    assert r1.replay is False
    assert r2.replay is True
    assert r2.compra.id == r1.compra.id
