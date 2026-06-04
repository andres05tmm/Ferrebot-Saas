"""Integración del repositorio de ventas contra una base efímera real (Postgres)."""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.ventas.errors import StockInsuficiente
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


def _venta(producto_id, cantidad, key=None):
    return VentaCrear(
        metodo_pago="efectivo",
        idempotency_key=key,
        lineas=[VentaDetalleCrear(producto_id=producto_id, cantidad=Decimal(cantidad))],
    )


async def test_registrar_venta_persiste_detalle_movimiento_y_descuenta_stock(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="11900", iva=19, stock="100")
        res = await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "2"), vendedor_id=uid)
        await s.commit()

    assert res.replay is False
    assert res.venta.consecutivo == 1            # primer nextval de la SEQUENCE
    assert res.venta.total == Decimal("23800.00")

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1
        assert (await s.execute(text("SELECT count(*) FROM ventas_detalle"))).scalar_one() == 1
        tipo, cant = (
            await s.execute(text("SELECT tipo, cantidad FROM movimientos_inventario"))
        ).one()
        assert tipo == "SALIDA"
        assert cant == Decimal("2.000")
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("98.000")   # 100 - 2


async def test_idempotencia_no_duplica_venta_ni_movimiento(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        r1 = await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3", key="dup"), uid)
        await s.commit()

    # Reintento con la MISMA clave en una sesión nueva: devuelve la existente, no crea otra.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3", key="dup"), uid)
        await s.commit()

    assert r2.replay is True
    assert r2.venta.id == r1.venta.id

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 1
        assert (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one() == 1
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("97.000")   # se descontó una sola vez (100 - 3)


async def test_stock_insuficiente_no_registra_nada(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="5")
        with pytest.raises(StockInsuficiente):
            await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "10"), uid)
        await s.rollback()

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one() == 0
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("5.000")   # intacto
