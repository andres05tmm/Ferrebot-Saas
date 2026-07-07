"""Compras del vertical construcción (Fase 3, spec 11): imputación a obra + resbalos + alerta de precios.

INVARIANTE crítico (test-primero, RED antes del fix): la compra imputada a OBRA (obra_id set) o marcada
como VIAJE DE MATERIAL (es_viaje_material) **NO mueve stock** (no genera `movimientos_inventario` ni toca
`inventario`/`precio_compra`); la compra de CATÁLOGO (sin obra, sin viaje) SIGUE moviendo stock como hoy.

Cubre además: resbalo calculado y persistido + alerta de baja rentabilidad (margen <5% o negativo), la
alerta de precio de proveedor (>15% sobre el promedio de 6 meses), validación (una compra de catálogo
exige `producto_id`) y aislamiento multi-tenant.
"""
from datetime import datetime
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.compras.repository import SqlComprasRepository
from modules.compras.router import router as compras_router
from modules.compras.schemas import CompraCrear
from modules.compras.service import ComprasService

# `compras.obra_id` es una FK a `obras` (tenant 0048): registra el modelo `Obra` en la metadata del ORM
# para que la FK resuelva al correr este archivo en aislamiento (en la suite completa ya lo carga otro test).
import modules.obra.models  # noqa: E402,F401  (side-effect: registra la tabla `obras`)


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

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pim", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pos"})  # expande a `inventario`
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


async def _seed_obra(s: AsyncSession, *, nombre: str = "Vía Rural") -> int:
    cid = (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id"))
    ).scalar_one()
    return (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, :n) RETURNING id"),
            {"c": cid, "n": nombre},
        )
    ).scalar_one()


async def _cuenta_movimientos(engine) -> int:
    async with AsyncSession(engine) as s:
        return (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one()


# ---- INVARIANTE: catálogo mueve stock, obra/viaje NO -----------------------
async def test_compra_catalogo_mueve_stock_pero_viaje_material_no(tenant):
    """Los DOS caminos en el mismo tenant: la de catálogo genera ENTRADA y sube stock; la de viaje de
    material no toca inventario (solo imputa/registra el resbalo)."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        pid = await _seed_producto(s, nombre="Cemento", stock="0")
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        # (1) Compra de CATÁLOGO: mueve stock como hoy.
        cat = await c.post(
            "/api/v1/compras",
            json={"proveedor": {"nombre": "Ferre Mayorista"},
                  "items": [{"producto_id": pid, "cantidad": 10, "costo": 8000}]},
        )
        # (2) VIAJE DE MATERIAL: NO mueve stock; calcula resbalo (sin producto de catálogo).
        viaje = await c.post(
            "/api/v1/compras",
            json={
                "proveedor": {"nombre": "Planta Asfalto"},
                "categoria": "MEZCLA_ASFALTICA",
                "es_viaje_material": True,
                "precio_venta_cliente": 1150000,
                "items": [{"cantidad": 20, "costo": 50000}],   # 20 m³ × 50.000 = 1.000.000
            },
        )
    assert cat.status_code == 201, cat.text
    assert cat.json()["mueve_stock"] is True
    assert viaje.status_code == 201, viaje.text
    body = viaje.json()
    assert body["mueve_stock"] is False
    assert body["es_viaje_material"] is True
    # resbalo = 1.150.000 − 1.000.000 = 150.000 (13,04% sobre la venta), sin alerta (>5%).
    assert body["resbalo"] == "150000.0000"
    assert body["resbalo_pct"] == "13.04"
    assert body["resbalo_alerta"] is False

    # SOLO la compra de catálogo dejó movimiento de inventario; el stock subió por ella y no por el viaje.
    async with AsyncSession(tenant.engine) as s:
        movs = (
            await s.execute(text("SELECT tipo, cantidad FROM movimientos_inventario WHERE producto_id=:p"), {"p": pid})
        ).all()
        assert len(movs) == 1 and movs[0].tipo == "ENTRADA"          # el viaje NO agregó movimiento
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock == Decimal("10.000")                            # 0 + 10 (solo la de catálogo)
        # El resbalo quedó PERSISTIDO en la compra de viaje.
        resbalo = (
            await s.execute(text("SELECT resbalo FROM compras WHERE es_viaje_material = true"))
        ).scalar_one()
        assert resbalo == Decimal("150000.0000")


async def test_compra_imputada_a_obra_no_mueve_stock(tenant):
    """La compra con `obra_id` (material comprado para la obra) se imputa pero NO entra al inventario,
    aunque la línea referencie un producto del catálogo."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        pid = await _seed_producto(s, nombre="Arena", stock="5")
        obra_id = await _seed_obra(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/compras",
            json={
                "proveedor": {"nombre": "Cantera"},
                "obra_id": obra_id,
                "categoria": "ARENA_AGREGADO",
                "items": [{"producto_id": pid, "cantidad": 3, "costo": 1500}],
            },
        )
    assert r.status_code == 201, r.text
    assert r.json()["obra_id"] == obra_id
    assert r.json()["mueve_stock"] is False

    async with AsyncSession(tenant.engine) as s:
        n_mov = (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one()
        assert n_mov == 0                                            # imputada a obra → cero inventario
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock == Decimal("5.000")                            # intacto (no sumó la compra)
        obra_persistida = (await s.execute(text("SELECT obra_id FROM compras"))).scalar_one()
        assert obra_persistida == obra_id


# ---- Resbalo: alerta de baja rentabilidad ----------------------------------
async def test_resbalo_alerta_margen_bajo(tenant):
    """Un viaje con margen < 5% dispara la alerta (pérdida silenciosa: márgenes de 3–4%)."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/compras",
            json={
                "proveedor": {"nombre": "Planta"},
                "categoria": "MEZCLA_ASFALTICA",
                "es_viaje_material": True,
                "precio_venta_cliente": 1020000,        # costo 1.000.000 → 20.000 (1,96% < 5%)
                "items": [{"cantidad": 1, "costo": 1000000}],
            },
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["resbalo"] == "20000.0000"
    assert body["resbalo_pct"] == "1.96"
    assert body["resbalo_alerta"] is True


async def test_viaje_material_sin_precio_venta_es_rechazado(tenant):
    """El contrato exige `precio_venta_cliente` cuando `es_viaje_material` (para poder computar el resbalo)."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()
    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/compras",
            json={"proveedor": {"nombre": "P"}, "es_viaje_material": True,
                  "items": [{"cantidad": 1, "costo": 1000}]},
        )
    assert r.status_code == 422, r.text


async def test_compra_catalogo_sin_producto_id_es_rechazada(tenant):
    """Una compra de catálogo (mueve stock) exige `producto_id` en cada ítem; sin él → 422."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()
    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/compras",
            json={"proveedor": {"nombre": "P"}, "items": [{"cantidad": 1, "costo": 1000}]},
        )
    assert r.status_code == 422, r.text


# ---- Alerta de precio de proveedor (>15% sobre promedio 6 meses) -----------
async def test_alerta_precio_proveedor_sobre_promedio_6m(tenant):
    """El tercer viaje al mismo proveedor+categoría llega 30% más caro que el promedio → alerta de precio."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    base = {"proveedor": {"nombre": "Planta Única"}, "categoria": "MEZCLA_ASFALTICA",
            "es_viaje_material": True, "precio_venta_cliente": 200000}
    async with _cliente(app) as c:
        # Dos compras a 100.000/u fijan el promedio histórico del proveedor.
        r1 = await c.post("/api/v1/compras", json={**base, "items": [{"cantidad": 1, "costo": 100000}]})
        r2 = await c.post("/api/v1/compras", json={**base, "items": [{"cantidad": 1, "costo": 100000}]})
        # Tercera a 130.000/u (30% sobre el promedio 100.000; umbral 15%) → alerta.
        r3 = await c.post("/api/v1/compras", json={**base, "items": [{"cantidad": 1, "costo": 130000}]})

    assert r1.json()["alerta_precio_proveedor"] is False   # sin historial
    assert r2.json()["alerta_precio_proveedor"] is False   # igual al promedio
    assert r3.json()["alerta_precio_proveedor"] is True, r3.text


# ---- Reporte de resbalos ---------------------------------------------------
async def test_reporte_resbalos_lista_viajes_con_margen(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        pid = await _seed_producto(s, nombre="Cemento", stock="0")
        await s.commit()
    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        # Un viaje (entra al reporte) + una compra de catálogo (NO entra).
        await c.post("/api/v1/compras", json={
            "proveedor": {"nombre": "Planta"}, "categoria": "MEZCLA_ASFALTICA",
            "es_viaje_material": True, "precio_venta_cliente": 1150000,
            "items": [{"cantidad": 1, "costo": 1000000}]})
        await c.post("/api/v1/compras", json={
            "proveedor": {"nombre": "Ferre"}, "items": [{"producto_id": pid, "cantidad": 1, "costo": 5000}]})
        rep = await c.get("/api/v1/compras/resbalos")
    assert rep.status_code == 200, rep.text
    filas = rep.json()
    assert len(filas) == 1                                  # solo el viaje de material
    assert filas[0]["resbalo"] == "150000.0000"
    assert filas[0]["resbalo_pct"] == "13.04"


# ---- Aislamiento multi-tenant ----------------------------------------------
async def test_aislamiento_compras_obra_entre_tenants(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        uid = await _seed_usuario(s)
        await s.commit()
        datos = CompraCrear(
            proveedor={"nombre": "Planta A"}, categoria="MEZCLA_ASFALTICA",
            es_viaje_material=True, precio_venta_cliente=Decimal("1150000"),
            items=[{"cantidad": Decimal("1"), "costo": Decimal("1000000")}],
        )
        await ComprasService(SqlComprasRepository(s)).registrar(datos, usuario_id=uid)
        await s.commit()

    async with AsyncSession(a.engine) as s:
        en_a = await SqlComprasRepository(s).listar(inicio=None, fin=None)
    async with AsyncSession(b.engine) as s:
        en_b = await SqlComprasRepository(s).listar(inicio=None, fin=None)
    assert len(en_a) == 1
    assert en_b == []                                       # la empresa B nunca ve la compra de A
