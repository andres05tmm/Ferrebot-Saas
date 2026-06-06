"""Edición de venta EN EL LUGAR (PUT /ventas/{id}) por HTTP contra base efímera real.

Patrón test_ventas_borrado: app mínima con el router de ventas + overrides de auth y de sesión del
tenant (commit en éxito, rollback en error → atomicidad). Cubre: editar de HOY ajusta stock (viejo
restaurado, nuevo aplicado), recalcula totales y conserva consecutivo; factura viva → 409; día
anterior → 409; vendedor ajeno → 403; producto inexistente → 404 sin cambiar nada; y venta_editada.
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


async def _seed_producto2(s: AsyncSession, *, nombre: str, precio: str, stock: str, iva: int = 19) -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES (:n,'unidad',:p,:iva,false,true) RETURNING id"
            ),
            {"n": nombre, "p": precio, "iva": iva},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


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


# ---- Edición feliz ---------------------------------------------------------
async def test_editar_de_hoy_ajusta_stock_recalcula_y_conserva_consecutivo(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid_a = await seed_producto(s, precio="10000", stock="100")
        pid_b = await _seed_producto2(s, nombre="Cemento", precio="20000", stock="50")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid_a, cantidad="2")
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        consecutivo = (await s.execute(text("SELECT consecutivo FROM ventas WHERE id=:v"), {"v": vid})).scalar_one()
    assert await _stock(tenant.engine, pid_a) == Decimal("98.000")   # 100 − 2 tras la venta original

    # Editar: cambia la línea de A→B (qty 3) y el método de pago.
    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.put(f"/api/v1/ventas/{vid}", json={
            "metodo_pago": "transferencia",
            "lineas": [{"producto_id": pid_b, "cantidad": 3}],
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == vid
    assert body["consecutivo"] == consecutivo          # MANTIENE el consecutivo
    assert body["metodo_pago"] == "transferencia"      # cabecera actualizada
    assert body["total"] == "60000.00"                 # 3 × 20000 (motor de precios)
    assert Decimal(body["subtotal"]) + Decimal(body["impuestos"]) == Decimal(body["total"])
    assert [ln["producto_id"] for ln in body["lineas"]] == [pid_b]

    # Stock: A restaurado (ya no está en la venta), B aplicado.
    assert await _stock(tenant.engine, pid_a) == Decimal("100.000")
    assert await _stock(tenant.engine, pid_b) == Decimal("47.000")   # 50 − 3
    async with AsyncSession(tenant.engine) as s:
        movs = (
            await s.execute(
                text("SELECT producto_id, tipo, cantidad FROM movimientos_inventario WHERE referencia=:r ORDER BY producto_id"),
                {"r": f"venta:{vid}"},
            )
        ).all()
        assert movs == [(pid_b, "SALIDA", Decimal("3.000"))]   # solo el SALIDA nuevo; el viejo (A) se borró


async def test_admin_edita_venta_ajena_de_hoy(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, precio="10000", stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        admin = await _otro_usuario(s, rol="admin")
        await s.commit()

    app = _app(tenant, user_id=admin, rol="admin")
    async with _cliente(app) as c:
        r = await c.put(f"/api/v1/ventas/{vid}", json={"metodo_pago": "efectivo", "lineas": [{"producto_id": pid, "cantidad": 5}]})
    assert r.status_code == 200, r.text
    assert await _stock(tenant.engine, pid) == Decimal("95.000")   # 100 (restaurado) − 5


# ---- Guards ----------------------------------------------------------------
async def test_editar_con_factura_viva_409_y_no_cambia(tenant, seed_producto):
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
        r = await c.put(f"/api/v1/ventas/{vid}", json={"metodo_pago": "efectivo", "lineas": [{"producto_id": pid, "cantidad": 5}]})
    assert r.status_code == 409, r.text
    assert "factura" in r.json()["detail"].lower()
    assert await _stock(tenant.engine, pid) == Decimal("98.000")   # intacto


async def test_editar_venta_de_dia_anterior_409(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        await s.execute(
            text("UPDATE ventas SET fecha = :f WHERE id=:v"), {"f": now_co() - timedelta(days=2), "v": vid}
        )
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.put(f"/api/v1/ventas/{vid}", json={"metodo_pago": "efectivo", "lineas": [{"producto_id": pid, "cantidad": 5}]})
    assert r.status_code == 409, r.text
    assert "del día" in r.json()["detail"]


async def test_vendedor_no_edita_venta_de_otro_403(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        otro = await _otro_usuario(s, rol="vendedor")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        await s.commit()

    app = _app(tenant, user_id=otro, rol="vendedor")
    async with _cliente(app) as c:
        r = await c.put(f"/api/v1/ventas/{vid}", json={"metodo_pago": "efectivo", "lineas": [{"producto_id": pid, "cantidad": 1}]})
    assert r.status_code == 403, r.text


async def test_editar_con_producto_inexistente_404_y_no_cambia_nada(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.put(f"/api/v1/ventas/{vid}", json={"metodo_pago": "efectivo", "lineas": [{"producto_id": 999999, "cantidad": 1}]})
    assert r.status_code == 404, r.text
    # Atomicidad: la transacción se revierte → la venta y su stock quedan como estaban.
    assert await _stock(tenant.engine, pid) == Decimal("98.000")
    async with AsyncSession(tenant.engine) as s:
        lineas = (await s.execute(text("SELECT producto_id FROM ventas_detalle WHERE venta_id=:v"), {"v": vid})).scalars().all()
        assert lineas == [pid]   # la línea original sigue intacta


# ---- Evento ----------------------------------------------------------------
async def test_editar_emite_venta_editada_e_inventario_actualizado(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="100")
        vid = await _seed_venta(s, vendedor_id=uid, producto_id=pid, cantidad="2")
        await s.commit()

    queue = await event_hub.subscribe(tenant_id=8811, dsn=tenant.url)
    try:
        app = _app(tenant, user_id=uid)
        async with _cliente(app) as c:
            r = await c.put(f"/api/v1/ventas/{vid}", json={"metodo_pago": "efectivo", "lineas": [{"producto_id": pid, "cantidad": 4}]})
        assert r.status_code == 200, r.text

        eventos = set()
        for _ in range(3):
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                break
            eventos.add(json.loads(payload)["event"])
        assert "venta_editada" in eventos
        assert "inventario_actualizado" in eventos
    finally:
        await event_hub.unsubscribe(8811, queue)
