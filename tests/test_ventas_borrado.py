"""Borrado de venta (DELETE /ventas/{id}) por HTTP contra base efímera real.

Patrón test_inventario_crud: app mínima con el router de ventas + overrides de auth y de sesión del
tenant (que hace commit, como get_tenant_db real, para que persista y se entregue el pg_notify).
Cubre: borrar venta de HOY sin factura (restaura stock, borra venta + SALIDA), factura viva → 409,
día anterior → 409, vendedor ajeno → 403, admin borra cualquiera, 404, y el evento venta_anulada.
"""
import asyncio
import json
from datetime import timedelta
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.config.timezone import now_co
from core.db.session import get_tenant_db
from core.events.hub import event_hub
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.router import router as ventas_router
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


def _app(tenant, *, user_id: int, rol: str = "vendedor") -> FastAPI:
    """App con el router de ventas y overrides de auth + sesión (que hace commit)."""
    app = FastAPI()
    app.include_router(ventas_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def _seed_venta(s: AsyncSession, *, vendedor_id: int, producto_id: int, cantidad: str = "2") -> int:
    datos = VentaCrear(
        metodo_pago="efectivo",
        lineas=[VentaDetalleCrear(producto_id=producto_id, cantidad=Decimal(cantidad))],
    )
    res = await VentaService(SqlVentasRepository(s)).registrar_venta(datos, vendedor_id=vendedor_id)
    return res.venta.id


async def _otro_usuario(s: AsyncSession, *, rol: str) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Otro', :r) RETURNING id"), {"r": rol}
        )
    ).scalar_one()


async def _stock(engine, pid: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()


async def _existe_venta(engine, vid: int) -> int:
    async with AsyncSession(engine) as s:
        return (await s.execute(text("SELECT count(*) FROM ventas WHERE id=:v"), {"v": vid})).scalar_one()


# ---- Borrado feliz ---------------------------------------------------------
async def test_borrar_venta_de_hoy_restaura_stock_y_elimina_todo(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("98.000")   # 100 − 2 tras la venta

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.delete(f"/api/v1/ventas/{vid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"venta_id": vid, "borrada": True}

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM ventas WHERE id=:v"), {"v": vid})).scalar_one() == 0
        assert (await s.execute(text("SELECT count(*) FROM ventas_detalle WHERE venta_id=:v"), {"v": vid})).scalar_one() == 0
        salidas = (
            await s.execute(text("SELECT count(*) FROM movimientos_inventario WHERE referencia=:r"), {"r": f"venta:{vid}"})
        ).scalar_one()
        assert salidas == 0   # el movimiento SALIDA de la venta también se borró
    assert await _stock(tenant.engine, pid) == Decimal("100.000")   # stock restaurado (neto cero)


async def test_admin_borra_venta_ajena_de_hoy(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="3")
        admin = await _otro_usuario(s, rol="admin")
        await s.commit()

    app = _app(tenant, user_id=admin, rol="admin")
    async with _cliente(app) as c:
        r = await c.delete(f"/api/v1/ventas/{vid}")
    assert r.status_code == 200, r.text
    assert await _existe_venta(tenant.engine, vid) == 0
    assert await _stock(tenant.engine, pid) == Decimal("100.000")


# ---- Guards ----------------------------------------------------------------
async def test_borrar_con_factura_viva_409_y_no_borra(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        await s.execute(
            text("INSERT INTO facturas_electronicas (venta_id, tipo, estado) VALUES (:v,'factura','aceptada')"),
            {"v": vid},
        )
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.delete(f"/api/v1/ventas/{vid}")
    assert r.status_code == 409, r.text
    assert "factura" in r.json()["detail"].lower()
    assert await _existe_venta(tenant.engine, vid) == 1
    assert await _stock(tenant.engine, pid) == Decimal("98.000")   # intacto: no se restauró


async def test_borrar_venta_de_dia_anterior_409(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        # Backdating: la venta pasa a ser de hace 2 días (hora Colombia).
        await s.execute(
            text("UPDATE ventas SET fecha = :f WHERE id=:v"), {"f": now_co() - timedelta(days=2), "v": vid}
        )
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.delete(f"/api/v1/ventas/{vid}")
    assert r.status_code == 409, r.text
    assert "del día" in r.json()["detail"]
    assert await _existe_venta(tenant.engine, vid) == 1


async def test_vendedor_no_borra_venta_de_otro_403(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")            # dueño de la venta
        otro = await _otro_usuario(s, rol="vendedor")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        await s.commit()

    app = _app(tenant, user_id=otro, rol="vendedor")              # otro vendedor intenta borrarla
    async with _cliente(app) as c:
        r = await c.delete(f"/api/v1/ventas/{vid}")
    assert r.status_code == 403, r.text
    assert await _existe_venta(tenant.engine, vid) == 1


async def test_borrar_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _otro_usuario(s, rol="vendedor")
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.delete("/api/v1/ventas/999999")
    assert r.status_code == 404, r.text


# ---- Evento ----------------------------------------------------------------
async def test_borrar_emite_venta_anulada_e_inventario_actualizado(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        await s.commit()

    queue = await event_hub.subscribe(tenant_id=8801, dsn=tenant.url)
    try:
        app = _app(tenant, user_id=uid)
        async with _cliente(app) as c:
            r = await c.delete(f"/api/v1/ventas/{vid}")
        assert r.status_code == 200, r.text

        eventos = set()
        for _ in range(3):
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                break
            eventos.add(json.loads(payload)["event"])
        assert "venta_anulada" in eventos
        assert "inventario_actualizado" in eventos
    finally:
        await event_hub.unsubscribe(8801, queue)
