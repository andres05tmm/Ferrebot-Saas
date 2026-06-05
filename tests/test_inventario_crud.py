"""CRUD de catálogo (Fase 12, Slice 1) por HTTP contra base efímera real.

Patrón test_smoke_routers_http: app FastAPI mínima + ASGITransport + overrides de auth y de sesión
del tenant. La sesión del override hace commit (como get_tenant_db real) para que persista y para que
el pg_notify del evento se entregue. Cubre: alta (con/sin stock_inicial → ENTRADA, regla #7),
fracciones, código duplicado (409), edición (reemplaza fracciones), soft-delete, RBAC y el evento.
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
from core.db.session import get_tenant_db
from core.events.hub import event_hub
from modules.inventario.router import router as inventario_router


def _app(tenant, *, user_id: int, rol: str = "admin") -> FastAPI:
    """App con el router de inventario y overrides de auth + sesión (que hace commit)."""
    app = FastAPI()
    app.include_router(inventario_router, prefix="/api/v1")

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


async def _seed_usuario(s: AsyncSession, *, rol: str = "admin") -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Quien', :r) RETURNING id"), {"r": rol}
        )
    ).scalar_one()


def _payload(**over) -> dict:
    base = {
        "nombre": "Taladro Bosch",
        "unidad_medida": "unidad",
        "precio_venta": "50000",
        "iva": 19,
        "permite_fraccion": False,
        "activo": True,
        "stock_minimo": "5",
    }
    base.update(over)
    return base


# ---- Alta ------------------------------------------------------------------
async def test_crear_sin_stock_inicial_crea_inventario_en_cero(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/productos", json=_payload(stock_minimo="7"))
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["activo"] is True

    async with AsyncSession(tenant.engine) as s:
        stock, minimo = (
            await s.execute(
                text("SELECT stock_actual, stock_minimo FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).one()
        assert stock == Decimal("0.000")
        assert minimo == Decimal("7.000")
        movs = (
            await s.execute(
                text("SELECT count(*) FROM movimientos_inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).scalar_one()
        assert movs == 0  # sin stock inicial → sin movimiento


async def test_crear_con_stock_inicial_registra_entrada(tenant):
    """Regla #7: el stock inicial entra por un movimiento ENTRADA, no por un set crudo."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/productos",
            json=_payload(stock_inicial="10", precio_compra="30000"),
        )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    async with AsyncSession(tenant.engine) as s:
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("10.000")
        tipo, cant, costo = (
            await s.execute(
                text(
                    "SELECT tipo, cantidad, costo_unitario FROM movimientos_inventario "
                    "WHERE producto_id=:p"
                ),
                {"p": pid},
            )
        ).one()
        assert tipo == "ENTRADA"
        assert cant == Decimal("10.000")
        assert costo == Decimal("30000.00")


async def test_crear_con_fracciones(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post(
            "/api/v1/productos",
            json=_payload(
                permite_fraccion=True,
                fracciones=[
                    {"fraccion": "1/2", "decimal": "0.5", "precio_total": "30000"},
                    {"fraccion": "1/4", "decimal": "0.25", "precio_total": "16000"},
                ],
            ),
        )
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    async with AsyncSession(tenant.engine) as s:
        fracs = (
            await s.execute(
                text("SELECT fraccion FROM productos_fracciones WHERE producto_id=:p ORDER BY fraccion"),
                {"p": pid},
            )
        ).scalars().all()
        assert fracs == ["1/2", "1/4"]


async def test_crear_codigo_duplicado_409(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/productos", json=_payload(codigo="TAL-001"))
        r2 = await c.post("/api/v1/productos", json=_payload(nombre="Otro", codigo="TAL-001"))
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text


# ---- Edición ---------------------------------------------------------------
async def test_editar_reemplaza_fracciones_y_no_toca_stock(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        creado = await c.post(
            "/api/v1/productos",
            json=_payload(
                stock_inicial="20",
                permite_fraccion=True,
                fracciones=[{"fraccion": "1/2", "decimal": "0.5", "precio_total": "30000"}],
            ),
        )
        pid = creado.json()["id"]
        r = await c.put(
            f"/api/v1/productos/{pid}",
            json=_payload(
                nombre="Taladro Editado",
                precio_venta="55000",
                stock_minimo="9",
                permite_fraccion=True,
                fracciones=[
                    {"fraccion": "1/3", "decimal": "0.333", "precio_total": "20000"},
                    {"fraccion": "2/3", "decimal": "0.666", "precio_total": "39000"},
                ],
            ),
        )
    assert r.status_code == 200, r.text
    assert r.json()["nombre"] == "Taladro Editado"

    async with AsyncSession(tenant.engine) as s:
        fracs = (
            await s.execute(
                text("SELECT fraccion FROM productos_fracciones WHERE producto_id=:p ORDER BY fraccion"),
                {"p": pid},
            )
        ).scalars().all()
        assert fracs == ["1/3", "2/3"]  # la original (1/2) se reemplazó
        stock, minimo = (
            await s.execute(
                text("SELECT stock_actual, stock_minimo FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).one()
        assert stock == Decimal("20.000")  # el PUT NO toca stock_actual
        assert minimo == Decimal("9.000")  # sí actualiza el mínimo


async def test_editar_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.put("/api/v1/productos/999999", json=_payload())
    assert r.status_code == 404, r.text


# ---- Soft delete -----------------------------------------------------------
async def test_soft_delete_marca_inactivo_y_sale_de_lista_activa(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        pid = (await c.post("/api/v1/productos", json=_payload())).json()["id"]
        r = await c.delete(f"/api/v1/productos/{pid}")
        activos = await c.get("/api/v1/productos", params={"activo": "true"})
    assert r.status_code == 200, r.text

    async with AsyncSession(tenant.engine) as s:
        activo = (
            await s.execute(text("SELECT activo FROM productos WHERE id=:p"), {"p": pid})
        ).scalar_one()
        assert activo is False  # soft delete: la fila sigue (la referencian ventas)
    assert pid not in [p["id"] for p in activos.json()]  # no aparece en el listado activo


async def test_soft_delete_inexistente_404(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.delete("/api/v1/productos/999999")
    assert r.status_code == 404, r.text


# ---- RBAC ------------------------------------------------------------------
async def test_crud_es_solo_admin_vendedor_403(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s, rol="vendedor")
        await s.commit()

    app = _app(tenant, user_id=uid, rol="vendedor")
    async with _cliente(app) as c:
        post = await c.post("/api/v1/productos", json=_payload())
        put = await c.put("/api/v1/productos/1", json=_payload())
        dele = await c.delete("/api/v1/productos/1")
    assert post.status_code == 403, post.text
    assert put.status_code == 403, put.text
    assert dele.status_code == 403, dele.text


# ---- Evento ----------------------------------------------------------------
async def test_crear_emite_inventario_actualizado(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    queue = await event_hub.subscribe(tenant_id=7777, dsn=tenant.url)
    try:
        app = _app(tenant, user_id=uid)
        async with _cliente(app) as c:
            r = await c.post("/api/v1/productos", json=_payload())
        assert r.status_code == 201, r.text

        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        evento = json.loads(payload)
        assert evento["event"] == "inventario_actualizado"
        assert evento["data"]["accion"] == "creado"
    finally:
        await event_hub.unsubscribe(7777, queue)


# ---- Validación ------------------------------------------------------------
async def test_validacion_montos_negativos_e_iva_fuera_de_rango_422(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        neg = await c.post("/api/v1/productos", json=_payload(precio_venta="-1"))
        iva = await c.post("/api/v1/productos", json=_payload(iva=200))
    assert neg.status_code == 422, neg.text
    assert iva.status_code == 422, iva.text
