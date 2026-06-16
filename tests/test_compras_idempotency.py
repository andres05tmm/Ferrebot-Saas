"""Idempotencia ESTRUCTURAL de compras (Fase 0, invariante no-negociable).

Un reintento con la misma `Idempotency-Key` NO duplica la compra ni su ENTRADA de inventario
(no negociable: "nada mueve stock sin movimiento", y un retry no debe duplicarlo). Misma key con
payload distinto → 409 (idempotencia_conflicto). Patrón HTTP de `test_compras.py` (base efímera real).
"""
import httpx
from decimal import Decimal

from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.compras.router import router as compras_router


def _app(tenant, *, user_id: int) -> FastAPI:
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

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol="admin")
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pos"})
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def _seed(s: AsyncSession) -> tuple[int, int]:
    uid = (await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Q','admin') RETURNING id"))).scalar_one()
    pid = (await s.execute(text(
        "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
        "VALUES ('Cemento','unidad',20000,19,false,true) RETURNING id"
    ))).scalar_one()
    await s.execute(text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,0,0)"), {"p": pid})
    return uid, pid


async def _conteos(engine, pid: int) -> tuple[int, int, Decimal]:
    async with AsyncSession(engine) as s:
        compras = (await s.execute(text("SELECT count(*) FROM compras"))).scalar_one()
        entradas = (await s.execute(
            text("SELECT count(*) FROM movimientos_inventario WHERE producto_id=:p AND tipo='ENTRADA'"),
            {"p": pid},
        )).scalar_one()
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
    return compras, entradas, stock


async def test_retry_misma_key_no_duplica_compra_ni_entrada(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid, pid = await _seed(s)
        await s.commit()

    body = {"proveedor": {"nombre": "Mayorista"}, "items": [{"producto_id": pid, "cantidad": 10, "costo": 8000}]}
    headers = {"Idempotency-Key": "compra-abc-123"}
    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/compras", json=body, headers=headers)
        r2 = await c.post("/api/v1/compras", json=body, headers=headers)   # reintento idéntico

    assert r1.status_code == 201, r1.text
    assert r2.status_code == 200, r2.text                 # replay: ya existía
    assert r1.json()["id"] == r2.json()["id"]             # misma compra original

    compras, entradas, stock = await _conteos(tenant.engine, pid)
    assert compras == 1                                   # NO se duplicó la compra
    assert entradas == 1                                  # NO se duplicó la ENTRADA de inventario
    assert stock == Decimal("10.000")                     # el stock subió una sola vez (regla #7)


async def test_misma_key_payload_distinto_es_conflicto(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid, pid = await _seed(s)
        await s.commit()

    headers = {"Idempotency-Key": "compra-xyz-9"}
    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/compras", json={
            "proveedor": {"nombre": "M"}, "items": [{"producto_id": pid, "cantidad": 10, "costo": 8000}],
        }, headers=headers)
        r2 = await c.post("/api/v1/compras", json={
            "proveedor": {"nombre": "M"}, "items": [{"producto_id": pid, "cantidad": 99, "costo": 8000}],
        }, headers=headers)

    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text                 # idempotencia_conflicto

    compras, entradas, _ = await _conteos(tenant.engine, pid)
    assert compras == 1 and entradas == 1                 # el conflicto NO registró nada
