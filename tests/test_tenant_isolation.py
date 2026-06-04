"""Aislamiento multi-tenant: la empresa A nunca ve datos de la empresa B (.claude/rules/testing.md).

Cada empresa es una base distinta; una venta en A no aparece al consultar B.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


async def _registrar(engine, seed_producto):
    async with AsyncSession(engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="50")
        datos = VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("1"))])
        await VentaService(SqlVentasRepository(s)).registrar_venta(datos, vendedor_id=uid)
        await s.commit()


async def _contar_ventas(engine) -> int:
    async with AsyncSession(engine) as s:
        return (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one()


async def test_empresa_A_no_ve_ventas_de_empresa_B(tenant_factory, seed_producto):
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()

    await _registrar(empresa_a.engine, seed_producto)   # venta solo en A

    assert await _contar_ventas(empresa_a.engine) == 1   # A tiene su venta
    assert await _contar_ventas(empresa_b.engine) == 0   # B no ve nada de A
