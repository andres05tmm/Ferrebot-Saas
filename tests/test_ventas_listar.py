"""E2 Parte 2 — GET /ventas (lista/historial) + GET /ventas/{id}.

Patrón test_facturacion_router: app mínima + ASGITransport + dependency_overrides. El repo es un
fake (captura los filtros recibidos); el scoping lo decide el `get_filtro_efectivo` REAL según el rol
del Principal inyectado. El filtrado por fecha real (SQL) se cubre en integración.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from modules.ventas.router import get_ventas_repo, router
from modules.ventas.schemas import VentaLeer


def _venta(vid: int = 1, vendedor_id: int = 5) -> VentaLeer:
    return VentaLeer(
        id=vid, consecutivo=vid, cliente_id=None, vendedor_id=vendedor_id,
        fecha=datetime(2026, 6, 4, 10, 0, 0), subtotal=Decimal("10000.00"),
        impuestos=Decimal("0.00"), total=Decimal("10000.00"), metodo_pago="efectivo",
        estado="completada", origen="web", idempotency_key=None,
    )


class _FakeVentasRepo:
    def __init__(self, ventas=None) -> None:
        self._ventas = ventas or []
        self._por_id: dict[int, VentaLeer] = {v.id: v for v in self._ventas}
        self.listar_args: dict | None = None

    async def listar(self, *, desde=None, hasta=None, vendedor_id=None):
        self.listar_args = {"desde": desde, "hasta": hasta, "vendedor_id": vendedor_id}
        return self._ventas

    async def obtener(self, venta_id: int):
        return self._por_id.get(venta_id)


def _app(repo: _FakeVentasRepo, *, rol: str = "vendedor", user_id: int = 5) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_ventas_repo] = lambda: repo
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_vendedor_solo_ve_lo_suyo():
    repo = _FakeVentasRepo([_venta(1)])
    app = _app(repo, rol="vendedor", user_id=5)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas", params={"vendedor_id": 99})  # intento de impersonar
    assert r.status_code == 200, r.text
    assert repo.listar_args["vendedor_id"] == 5                         # ignorado: ve solo lo suyo


async def test_admin_ve_todo():
    repo = _FakeVentasRepo([_venta(1), _venta(2)])
    app = _app(repo, rol="admin", user_id=1)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas")
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2
    assert repo.listar_args["vendedor_id"] is None


async def test_admin_impersona_con_vendedor_id():
    repo = _FakeVentasRepo([])
    app = _app(repo, rol="admin", user_id=1)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas", params={"vendedor_id": 7})
    assert r.status_code == 200, r.text
    assert repo.listar_args["vendedor_id"] == 7


async def test_pasa_filtro_de_fechas():
    repo = _FakeVentasRepo([])
    app = _app(repo, rol="admin", user_id=1)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas", params={"desde": "2026-06-01", "hasta": "2026-06-02"})
    assert r.status_code == 200, r.text
    assert repo.listar_args["desde"] == date(2026, 6, 1)
    assert repo.listar_args["hasta"] == date(2026, 6, 2)


async def test_obtener_200_y_404():
    repo = _FakeVentasRepo([_venta(1)])
    app = _app(repo)
    async with _cliente(app) as c:
        ok = await c.get("/api/v1/ventas/1")
        falta = await c.get("/api/v1/ventas/999")
    assert ok.status_code == 200, ok.text
    assert ok.json()["id"] == 1
    assert falta.status_code == 404


async def test_obtener_vendedor_no_ve_la_de_otro_404():
    repo = _FakeVentasRepo([_venta(2, vendedor_id=99)])     # venta de OTRO vendedor
    app = _app(repo, rol="vendedor", user_id=5)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/2")
    assert r.status_code == 404                              # 404, no 403 (no revela existencia)


async def test_obtener_admin_ve_cualquiera_200():
    repo = _FakeVentasRepo([_venta(2, vendedor_id=99)])
    app = _app(repo, rol="admin", user_id=1)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/2")
    assert r.status_code == 200, r.text
    assert r.json()["id"] == 2
