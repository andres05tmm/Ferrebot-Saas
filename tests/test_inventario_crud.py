"""CRUD de catálogo (rediseño de producto) por HTTP contra base efímera real.

Patrón test_smoke_routers_http: app FastAPI mínima + ASGITransport + overrides de auth y de sesión
del tenant (que hace commit, como get_tenant_db real, para que persista y se entregue el pg_notify).
Cubre: alta (inventario nace en 0, sin movimiento), proveedor_id válido/ inválido (422),
precio_especial, fracciones, código duplicado (409), edición (reemplaza fracciones, no toca stock),
categorías, soft-delete, RBAC, conteo físico y el evento.
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
from modules.inventario.router import router as inventario_router, router_catalogo


def _app(tenant, *, user_id: int, rol: str = "admin") -> FastAPI:
    """App con los routers de catálogo e inventario y overrides de auth + sesión (que hace commit)."""
    app = FastAPI()
    app.include_router(router_catalogo, prefix="/api/v1")
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
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pos"})  # router POS (ADR 0008)
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


async def _seed_proveedor(s: AsyncSession, *, nombre: str = "Ferre SAS", nit: str = "900.111") -> int:
    return (
        await s.execute(
            text("INSERT INTO proveedores (nombre, nit) VALUES (:n, :nit) RETURNING id"),
            {"n": nombre, "nit": nit},
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
    }
    base.update(over)
    return base


# ---- Alta ------------------------------------------------------------------
async def test_crear_nace_con_inventario_en_cero(tenant):
    """El producto nace con inventario en 0 (stock y mínimo) y SIN movimiento de inventario."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/productos", json=_payload())
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["activo"] is True
    assert r.json()["proveedor_id"] is None and r.json()["proveedor_nombre"] is None

    async with AsyncSession(tenant.engine) as s:
        stock, minimo = (
            await s.execute(
                text("SELECT stock_actual, stock_minimo FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).one()
        assert stock == Decimal("0.000")
        assert minimo == Decimal("0.000")
        movs = (
            await s.execute(
                text("SELECT count(*) FROM movimientos_inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).scalar_one()
        assert movs == 0  # nace en 0 → sin movimiento


async def test_crear_con_proveedor_valido_persiste_y_devuelve_nombre(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        prov_id = await _seed_proveedor(s, nombre="Distribuidora Andina")
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/productos", json=_payload(proveedor_id=prov_id))
    assert r.status_code == 201, r.text
    assert r.json()["proveedor_id"] == prov_id
    assert r.json()["proveedor_nombre"] == "Distribuidora Andina"

    async with AsyncSession(tenant.engine) as s:
        guardado = (
            await s.execute(text("SELECT proveedor_id FROM productos WHERE id=:p"), {"p": r.json()["id"]})
        ).scalar_one()
        assert guardado == prov_id


async def test_crear_con_proveedor_invalido_422(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/productos", json=_payload(proveedor_id=999999))
    assert r.status_code == 422, r.text


async def test_precio_especial_persiste(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/productos", json=_payload(precio_especial="45000", precio_compra="30000"))
    assert r.status_code == 201, r.text
    # El módulo no cuantiza precios: la respuesta refleja la escala de entrada (comparar numéricamente).
    assert Decimal(r.json()["precio_especial"]) == Decimal("45000")
    assert Decimal(r.json()["precio_compra"]) == Decimal("30000")

    async with AsyncSession(tenant.engine) as s:
        especial = (
            await s.execute(text("SELECT precio_especial FROM productos WHERE id=:p"), {"p": r.json()["id"]})
        ).scalar_one()
        assert especial == Decimal("45000.00")


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


# ---- Categorías ------------------------------------------------------------
async def test_categorias_distinct_lista_existentes(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        await c.post("/api/v1/productos", json=_payload(nombre="A", categoria="Herramientas"))
        await c.post("/api/v1/productos", json=_payload(nombre="B", categoria="Pinturas"))
        await c.post("/api/v1/productos", json=_payload(nombre="C", categoria="Herramientas"))  # repetida
        await c.post("/api/v1/productos", json=_payload(nombre="D"))  # sin categoría → no aparece
        r = await c.get("/api/v1/productos/categorias")
    assert r.status_code == 200, r.text
    assert r.json() == ["Herramientas", "Pinturas"]  # DISTINCT, sin nulos, ordenadas


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
                permite_fraccion=True,
                fracciones=[{"fraccion": "1/2", "decimal": "0.5", "precio_total": "30000"}],
            ),
        )
        pid = creado.json()["id"]
        # El stock se fija por conteo (no por el alta): lo dejamos en 20 para comprobar que el PUT no lo toca.
        await c.post("/api/v1/inventario/conteo", json={"producto_id": pid, "cantidad_contada": 20})
        r = await c.put(
            f"/api/v1/productos/{pid}",
            json=_payload(
                nombre="Taladro Editado",
                precio_venta="55000",
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
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
        assert stock == Decimal("20.000")  # el PUT NO toca stock_actual


async def test_editar_con_proveedor_invalido_422(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        prov_id = await _seed_proveedor(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        pid = (await c.post("/api/v1/productos", json=_payload(proveedor_id=prov_id))).json()["id"]
        ok = await c.put(f"/api/v1/productos/{pid}", json=_payload(proveedor_id=prov_id))
        mal = await c.put(f"/api/v1/productos/{pid}", json=_payload(proveedor_id=888888))
    assert ok.status_code == 200, ok.text
    assert mal.status_code == 422, mal.text


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


# ---- Conteo físico (set-to-absolute) ---------------------------------------
async def test_conteo_fija_stock_a_lo_contado_via_http(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        creado = await c.post("/api/v1/productos", json=_payload())  # nace en 0
        pid = creado.json()["id"]
        r = await c.post(
            "/api/v1/inventario/conteo",
            json={"producto_id": pid, "cantidad_contada": 25, "motivo": "conteo físico"},
            headers={"Idempotency-Key": "ct-1"},
        )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["stock_actual"] == "25.000" and body["delta"] == "25.000"

    async with AsyncSession(tenant.engine) as s:
        stock = (await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})).scalar_one()
        assert stock == Decimal("25.000")
        tipo = (await s.execute(text("SELECT tipo FROM movimientos_inventario WHERE producto_id=:p AND tipo='AJUSTE'"), {"p": pid})).scalar_one()
        assert tipo == "AJUSTE"


async def test_conteo_es_solo_admin_vendedor_403(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s, rol="vendedor")
        await s.commit()

    app = _app(tenant, user_id=uid, rol="vendedor")
    async with _cliente(app) as c:
        r = await c.post("/api/v1/inventario/conteo", json={"producto_id": 1, "cantidad_contada": 5})
    assert r.status_code == 403, r.text


async def test_conteo_emite_inventario_actualizado(tenant):
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        pid = (await c.post("/api/v1/productos", json=_payload())).json()["id"]

    queue = await event_hub.subscribe(tenant_id=7778, dsn=tenant.url)
    try:
        async with _cliente(app) as c:
            r = await c.post("/api/v1/inventario/conteo", json={"producto_id": pid, "cantidad_contada": 3})
        assert r.status_code == 201, r.text

        eventos = set()
        # Llega el inventario_actualizado del AJUSTE del conteo (3 ≠ 0 → delta +3).
        for _ in range(2):
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                break
            eventos.add(json.loads(payload)["event"])
        assert "inventario_actualizado" in eventos
    finally:
        await event_hub.unsubscribe(7778, queue)


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


# ---- Frecuentes ------------------------------------------------------------
async def test_frecuentes_ordena_por_mas_vendido(tenant):
    """GET /productos/frecuentes devuelve los más vendidos (por nº de líneas) primero; [] sin ventas."""
    async with AsyncSession(tenant.engine) as s:
        uid = await _seed_usuario(s)
        cemento = (await s.execute(text(
            "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
            "VALUES ('Cemento', 'unidad', 10000, 0, false, true) RETURNING id"))).scalar_one()
        puntilla = (await s.execute(text(
            "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
            "VALUES ('Puntilla', 'unidad', 500, 0, false, true) RETURNING id"))).scalar_one()
        v = (await s.execute(text(
            "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
            "metodo_pago, estado, origen) VALUES (1, :u, now(), 1000, 0, 1000, 'efectivo', "
            "'completada', 'web') RETURNING id"), {"u": uid})).scalar_one()
        for pid in (cemento, cemento, puntilla):   # cemento 2 líneas, puntilla 1
            await s.execute(text(
                "INSERT INTO ventas_detalle (venta_id, producto_id, cantidad, precio_unitario, iva) "
                "VALUES (:v, :p, 1, 1, 0)"), {"v": v, "p": pid})
        await s.commit()

    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/productos/frecuentes?dias=30&limite=12")
    assert r.status_code == 200, r.text
    nombres = [p["nombre"] for p in r.json()]
    assert nombres[:2] == ["Cemento", "Puntilla"]   # orden por frecuencia
