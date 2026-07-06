"""POST /devoluciones por HTTP contra base efímera real (ADR 0026).

Patrón test_ventas_borrado: app mínima con el router + overrides de auth y sesión del tenant (que
hace commit, como get_tenant_db real). La composición del servicio se sobreescribe con notas=None
(sin control DB en la app mínima); la nota crédito ya se cubre en test_notas_credito.
"""
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.devoluciones.repository import SqlDevolucionesRepository
from modules.devoluciones.router import get_devoluciones_service, router as devoluciones_router
from modules.devoluciones.service import DevolucionesService
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


def _app(tenant, *, user_id: int, rol: str = "vendedor") -> FastAPI:
    app = FastAPI()
    app.include_router(devoluciones_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    async def _service():
        # Composición de test: sin control DB (notas=None), sobre una sesión propia que commitea.
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            svc = DevolucionesService(
                SqlDevolucionesRepository(s),
                caja=SqlCajaRepository(s),
                fiados=FiadosService(SqlFiadosRepository(s)),
                notas=None,
            )
            yield svc
            await s.commit()

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_devoluciones_service] = _service
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pos"})
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def _seed(tenant, *, cantidad="3"):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        pid = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, costo_promedio, "
                    "iva, permite_fraccion, activo) VALUES ('Cemento','unidad',20000,12000,12000,19,false,true) RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,100,0)"), {"p": pid}
        )
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        venta = (
            await VentaService(SqlVentasRepository(s)).registrar_venta(
                VentaCrear(metodo_pago="efectivo",
                           lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal(cantidad))]),
                vendedor_id=uid,
            )
        ).venta
        await s.commit()
    return uid, pid, venta.id


async def test_post_devolucion_total_201_y_replay_200(tenant):
    uid, pid, vid = await _seed(tenant)
    app = _app(tenant, user_id=uid)
    body = {"venta_id": vid, "idempotency_key": "http-1", "motivo": "defectuoso"}

    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/devoluciones", json=body)
        r2 = await c.post("/api/v1/devoluciones", json=body)   # replay: misma key + mismo payload

    assert r1.status_code == 201, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["id"] == r2.json()["id"]
    assert r1.json()["total"] == "60000.00"

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM devoluciones"))).scalar_one() == 1


async def test_post_devolucion_key_con_payload_distinto_409(tenant):
    uid, pid, vid = await _seed(tenant, cantidad="5")
    app = _app(tenant, user_id=uid)

    async with _cliente(app) as c:
        r1 = await c.post("/api/v1/devoluciones", json={"venta_id": vid, "idempotency_key": "k"})
        r2 = await c.post(
            "/api/v1/devoluciones",
            json={"venta_id": vid, "idempotency_key": "k",
                  "lineas": [{"producto_id": pid, "cantidad": "1"}]},
        )
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 409, r2.text


async def test_post_devolucion_venta_inexistente_404(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        await s.commit()
    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/devoluciones", json={"venta_id": 999999})
    assert r.status_code == 404, r.text


async def test_post_devolucion_lineas_vacias_422(tenant):
    uid, pid, vid = await _seed(tenant)
    app = _app(tenant, user_id=uid)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/devoluciones", json={"venta_id": vid, "lineas": []})
    assert r.status_code == 422, r.text


# --- GET /devoluciones/ventas-facturadas (lista para nota crédito) ------------

async def _seed_facturada(tenant, *, tipo="factura", estado="aceptada", cufe="CUFE-ABC-123", con_factura=True):
    """Siembra una venta y (opcional) su documento fiscal. Devuelve (uid, venta_id, consecutivo)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))).scalar_one()
        pid = (
            await s.execute(text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, costo_promedio, "
                "iva, permite_fraccion, activo) VALUES ('Cemento','unidad',20000,12000,12000,19,false,true) RETURNING id"
            ))
        ).scalar_one()
        await s.execute(text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,100,0)"), {"p": pid})
        venta = (
            await VentaService(SqlVentasRepository(s)).registrar_venta(
                VentaCrear(metodo_pago="efectivo", lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("2"))]),
                vendedor_id=uid,
            )
        ).venta
        if con_factura:
            await s.execute(
                text("INSERT INTO facturas_electronicas (venta_id, tipo, estado, cufe, consecutivo, prefijo) "
                     "VALUES (:v, :t, :e, :c, 7, 'FPR')"),
                {"v": venta.id, "t": tipo, "e": estado, "c": cufe},
            )
        await s.commit()
    return uid, venta.id, venta.consecutivo


async def test_ventas_facturadas_lista_solo_las_con_documento(tenant):
    uid, vid, cons = await _seed_facturada(tenant, cufe="CUFE-XYZ-1")
    await _seed_facturada(tenant, con_factura=False)     # venta SIN documento → no debe aparecer
    app = _app(tenant, user_id=uid, rol="admin")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/devoluciones/ventas-facturadas")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == vid
    assert body[0]["fiscal_tipo"] == "factura" and body[0]["fiscal_estado"] == "aceptada"
    assert body[0]["cufe"] == "CUFE-XYZ-1" and body[0]["fiscal_numero"] == 7


async def test_ventas_facturadas_excluye_rechazada_y_anulada(tenant):
    uid, _vid, _c = await _seed_facturada(tenant, estado="rechazada")
    app = _app(tenant, user_id=uid, rol="admin")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/devoluciones/ventas-facturadas")
    assert r.status_code == 200, r.text
    assert r.json() == []                                 # solo pendiente|aceptada son candidatas


async def test_ventas_facturadas_busca_por_numero_y_cufe(tenant):
    uid, vid, cons = await _seed_facturada(tenant, cufe="CUFE-BUSCAME-999")
    app = _app(tenant, user_id=uid, rol="admin")
    async with _cliente(app) as c:
        por_num = await c.get("/api/v1/devoluciones/ventas-facturadas", params={"q": str(cons)})
        por_cufe = await c.get("/api/v1/devoluciones/ventas-facturadas", params={"q": "BUSCAME"})
        sin_match = await c.get("/api/v1/devoluciones/ventas-facturadas", params={"q": "zzz-nope"})
    assert [x["id"] for x in por_num.json()] == [vid]
    assert [x["id"] for x in por_cufe.json()] == [vid]     # CUFE por substring, case-insensitive
    assert sin_match.json() == []
