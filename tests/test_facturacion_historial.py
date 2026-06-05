"""Slice 3 — historial de facturación: repo (listar/detalle) en integración + router gateado.

Integración: contra base efímera real (consecutivo, estados, total de la venta ligada, extracción del
motivo desde dian_respuesta). Router: app mínima + ASGITransport + overrides (patrón
test_facturacion_router), verificando el gate de feature (404) y los shapes de salida.
"""
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.facturacion.repository import FacturaDetalle, FacturaLeer, SqlFacturacionRepository
from modules.facturacion.router import get_facturacion_repo, router


# ---- Integración (repo real contra Postgres efímero) -----------------------
async def _crear_pendiente(repo, *, key, venta_id=None):
    consecutivo = await repo.siguiente_consecutivo()
    return await repo.crear_pendiente(
        venta_id=venta_id, tipo="factura", prefijo="FPR",
        consecutivo=consecutivo, idempotency_key=key,
    )


async def test_listar_ordena_y_filtra_por_estado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        f1 = await _crear_pendiente(repo, key="k1")
        f2 = await _crear_pendiente(repo, key="k2")
        await s.commit()
        await repo.marcar_rechazada(f1.id, error_msg="x", dian_respuesta={"rechazo": "x"})
        await s.commit()
        todas = await repo.listar()
        solo_rechazadas = await repo.listar(estado="rechazada")

    assert [f.id for f in todas] == [f2.id, f1.id]          # más reciente (id) primero
    assert all(f.creado_en is not None for f in todas)      # el ORM trae la fecha
    assert {f.id for f in solo_rechazadas} == {f1.id}


async def test_detalle_trae_total_y_motivo_de_rechazo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        cons = (await s.execute(text("SELECT nextval('ventas_consecutivo_seq')"))).scalar_one()
        vid = (
            await s.execute(
                text(
                    "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago) "
                    "VALUES (:c,:u, now(), 10000, 1900, 11900, 'efectivo') RETURNING id"
                ),
                {"c": cons, "u": uid},
            )
        ).scalar_one()
        f = await _crear_pendiente(repo, key="k-rech", venta_id=vid)
        await s.commit()
        await repo.marcar_rechazada(
            f.id, error_msg="NIT inválido",
            dian_respuesta={"rechazo": "NIT del adquirente inválido"},
        )
        await s.commit()
        det = await repo.detalle(f.id)
        falta = await repo.detalle(999999)

    assert det is not None
    assert det.estado == "rechazada"
    assert det.total == Decimal("11900.00")                 # total de la venta ligada
    assert det.motivo == "NIT del adquirente inválido"      # extraído de dian_respuesta
    assert det.emitido_en is not None
    assert falta is None


async def test_detalle_sin_venta_total_none(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlFacturacionRepository(s)
        f = await _crear_pendiente(repo, key="k-sin-venta", venta_id=None)
        await s.commit()
        det = await repo.detalle(f.id)
    assert det is not None
    assert det.total is None
    assert det.motivo is None                               # pendiente: aún sin motivo


# ---- Router (gate de feature + shapes) -------------------------------------
class _FakeRepo:
    def __init__(self, *, lista=None, detalle=None) -> None:
        self._lista = lista or []
        self._detalle = detalle
        self.filtros: object = "UNSET"

    async def listar(self, *, desde=None, hasta=None, estado=None):
        self.filtros = (desde, hasta, estado)
        return self._lista

    async def detalle(self, factura_id):
        if self._detalle is not None and self._detalle.id == factura_id:
            return self._detalle
        return None


def _app(caps: frozenset[str], repo: _FakeRepo) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_facturacion_repo] = lambda: repo
    app.dependency_overrides[get_capacidades] = lambda: caps
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol="vendedor")
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


_FEATURE = frozenset({"facturacion_electronica"})


async def test_get_facturas_lista():
    lista = [
        FacturaLeer(id=2, venta_id=20, tipo="factura", prefijo="FPR", consecutivo=2, cufe=None,
                    estado="pendiente", idempotency_key="k2", intentos=0),
        FacturaLeer(id=1, venta_id=10, tipo="factura", prefijo="FPR", consecutivo=1, cufe="CUFE123",
                    estado="aceptada", idempotency_key="k1", intentos=0),
    ]
    app = _app(_FEATURE, _FakeRepo(lista=lista))
    async with _cliente(app) as c:
        r = await c.get("/api/v1/facturas", params={"estado": "pendiente"})
    assert r.status_code == 200, r.text
    assert [f["id"] for f in r.json()] == [2, 1]


async def test_get_factura_detalle_trae_motivo():
    det = FacturaDetalle(
        id=1, venta_id=10, tipo="factura", prefijo="FPR", consecutivo=1, cufe=None,
        estado="rechazada", idempotency_key="k1", intentos=0, creado_en=None,
        emitido_en=None, total=Decimal("11900.00"), motivo="NIT del adquirente inválido",
    )
    app = _app(_FEATURE, _FakeRepo(detalle=det))
    async with _cliente(app) as c:
        ok = await c.get("/api/v1/facturas/1")
        falta = await c.get("/api/v1/facturas/999")
    assert ok.status_code == 200, ok.text
    assert ok.json()["motivo"] == "NIT del adquirente inválido"
    assert ok.json()["total"] == "11900.00"
    assert falta.status_code == 404


async def test_get_facturas_sin_feature_404():
    app = _app(frozenset(), _FakeRepo(lista=[]))
    async with _cliente(app) as c:
        lista = await c.get("/api/v1/facturas")
        detalle = await c.get("/api/v1/facturas/1")
    assert lista.status_code == 404, lista.text
    assert detalle.status_code == 404, detalle.text
