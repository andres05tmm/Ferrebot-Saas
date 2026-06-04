"""SSE por empresa: la venta emite pg_notify y el hub lo entrega a un suscriptor (tenancy.md §6)."""
import asyncio
import json
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.events.hub import event_hub
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


async def test_venta_emite_evento_a_suscriptor(tenant, seed_producto):
    queue = await event_hub.subscribe(tenant_id=4242, dsn=tenant.url)
    try:
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            uid, pid = await seed_producto(s, stock="10")
            datos = VentaCrear(
                metodo_pago="efectivo",
                lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("1"))],
            )
            await VentaService(SqlVentasRepository(s)).registrar_venta(datos, vendedor_id=uid)
            await s.commit()   # el NOTIFY se entrega al COMMIT

        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        evento = json.loads(payload)
        assert evento["event"] == "venta_registrada"
        assert evento["data"]["consecutivo"] == 1
    finally:
        await event_hub.unsubscribe(4242, queue)
