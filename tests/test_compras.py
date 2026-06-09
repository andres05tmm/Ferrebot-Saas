"""Compras (Fase 12, Slice 4a) por HTTP contra base efímera real.

Patrón test_inventario_crud: app mínima + ASGITransport + overrides de auth y sesión del tenant (que
hace commit, para persistir y entregar el pg_notify). Cubre: registrar compra (crea compra+detalle,
ENTRADA que SUMA stock —regla #7—, fija productos.precio_compra), total calculado en el servidor,
get-or-create de proveedor, admin-only, listado por rango y emisión del evento.
"""
import asyncio
import json
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from core.events.hub import event_hub
from modules.compras.router import router as compras_router


def _app(tenant, *, user_id: int, rol: str = "admin") -> FastAPI:
    app = FastAPI()
    app.include_router(compras_router, prefix="/api/v1")

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
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pos"})  # router POS (ADR 0008)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def _seed_usuario(s: AsyncSession, *, rol: str = "admin") -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Quien', :r) RETURNING id"), {"r": rol})
    ).scalar_one()


async def _seed_producto(s: AsyncSession, *, nombre: str, stock: str = "0") -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES (:n,'unidad',20000,19,false,true) RETURNING id"
            ),
            {"n": nombre},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


# ---- Registrar -------------------------------------------------------------
async def test_registrar_compra_suma_stock_y_fija_precio_compra(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        pid = await _seed_producto(s, nombre="Cemento", stock="0")
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/compras",
            json={
                "proveedor": {"nombre": "Ferre Mayorista", "nit": "900111"},
                "items": [{"producto_id": pid, "cantidad": 10, "costo": 8000}],
            },
        )
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "80000.00"          # 10 × 8000, calculado en el servidor
    assert r.json()["proveedor_nombre"] == "Ferre Mayorista"

    async with AsyncSession(tenant.engine) as s:
        n_compras = (await s.execute(text("SELECT count(*) FROM compras"))).scalar_one()
        n_detalle = (await s.execute(text("SELECT count(*) FROM compras_detalle WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert n_compras == 1 and n_detalle == 1
        tipo, cant, costo = (
            await s.execute(
                text("SELECT tipo, cantidad, costo_unitario FROM movimientos_inventario WHERE producto_id=:p"),
                {"p": pid},
            )
        ).one()
        assert tipo == "ENTRADA" and cant == Decimal("10.000") and costo == Decimal("8000.00")
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock == Decimal("10.000")            # 0 + 10 (regla #7: la ENTRADA sube el stock)
        precio_compra = (await s.execute(text("SELECT precio_compra FROM productos WHERE id=:p"), {"p": pid})).scalar_one()
        assert precio_compra == Decimal("8000.00")   # fija el costo de compra del producto


async def test_total_multiitem_lo_calcula_el_servidor(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        p1 = await _seed_producto(s, nombre="Cemento", stock="0")
        p2 = await _seed_producto(s, nombre="Arena", stock="5")
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/compras",
            json={
                "proveedor": {"nombre": "Mayorista"},
                "items": [
                    {"producto_id": p1, "cantidad": 10, "costo": 8000},   # 80000
                    {"producto_id": p2, "cantidad": 3, "costo": 1500},    #  4500
                ],
            },
        )
    assert r.status_code == 201, r.text
    assert r.json()["total"] == "84500.00"

    async with AsyncSession(tenant.engine) as s:
        s1 = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": p1})).scalar_one()
        s2 = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": p2})).scalar_one()
        assert s1 == Decimal("10.000") and s2 == Decimal("8.000")   # 5 + 3


async def test_get_or_create_proveedor_dedup(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        pid = await _seed_producto(s, nombre="Cemento", stock="0")
        await s.commit()

    app = _app(tenant, user_id=uid)
    body = {"proveedor": {"nombre": "Distribuidora Norte"}, "items": [{"producto_id": pid, "cantidad": 1, "costo": 100}]}
    async with _cliente(app) as c:
        await c.post("/api/v1/compras", json=body)
        await c.post("/api/v1/compras", json={**body, "items": [{"producto_id": pid, "cantidad": 2, "costo": 120}]})

    async with AsyncSession(tenant.engine) as s:
        n = (await s.execute(text("SELECT count(*) FROM proveedores WHERE nombre='Distribuidora Norte'"))).scalar_one()
        assert n == 1   # el segundo registro reusa el proveedor existente


# ---- RBAC ------------------------------------------------------------------
async def test_registrar_compra_es_solo_admin_vendedor_403(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s, rol="vendedor")
        pid = await _seed_producto(s, nombre="Cemento", stock="0")
        await s.commit()

    app = _app(tenant, user_id=uid, rol="vendedor")
    async with _cliente(app) as c:
        post = await c.post("/api/v1/compras", json={"proveedor": {"nombre": "X"}, "items": [{"producto_id": pid, "cantidad": 1, "costo": 100}]})
        lista = await c.get("/api/v1/compras")
    assert post.status_code == 403, post.text
    assert lista.status_code == 403, lista.text


# ---- Listado ---------------------------------------------------------------
async def test_listar_compras_por_rango(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        pid = await _seed_producto(s, nombre="Cemento", stock="0")
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        await c.post("/api/v1/compras", json={"proveedor": {"nombre": "Prov A"}, "items": [{"producto_id": pid, "cantidad": 1, "costo": 100}]})
        await c.post("/api/v1/compras", json={"proveedor": {"nombre": "Prov B"}, "items": [{"producto_id": pid, "cantidad": 2, "costo": 200}]})
        # Default = mes en curso → ambas presentes.
        actual = await c.get("/api/v1/compras")
        # Rango pasado → vacío.
        viejo = await c.get("/api/v1/compras", params={"desde": "2020-01-01", "hasta": "2020-01-31"})
    assert actual.status_code == 200, actual.text
    assert len(actual.json()) == 2
    assert [x["proveedor_nombre"] for x in actual.json()] == ["Prov B", "Prov A"]   # más reciente primero
    assert viejo.json() == []


# ---- Evento ----------------------------------------------------------------
async def test_registrar_compra_emite_evento(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        pid = await _seed_producto(s, nombre="Cemento", stock="0")
        await s.commit()

    queue = await event_hub.subscribe(tenant_id=8181, dsn=tenant.url)
    try:
        app = _app(tenant, user_id=uid)
        async with _cliente(app) as c:
            r = await c.post("/api/v1/compras", json={"proveedor": {"nombre": "Prov"}, "items": [{"producto_id": pid, "cantidad": 1, "costo": 100}]})
        assert r.status_code == 201, r.text

        eventos = set()
        # Llegan 'compra_registrada' e 'inventario_actualizado' (dos NOTIFY en el mismo commit).
        for _ in range(2):
            payload = await asyncio.wait_for(queue.get(), timeout=5.0)
            eventos.add(json.loads(payload)["event"])
        assert "compra_registrada" in eventos
    finally:
        await event_hub.unsubscribe(8181, queue)
