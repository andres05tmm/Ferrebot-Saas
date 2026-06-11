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
from core.auth.features import get_capacidades
from modules.facturacion.repository import EstadoFiscalVenta
from modules.ventas.router import get_facturacion_lectura, get_ventas_repo, router
from modules.ventas.schemas import VentaConLineas, VentaDetalleLeer, VentaLeer


def _venta(vid: int = 1, vendedor_id: int = 5) -> VentaLeer:
    return VentaLeer(
        id=vid, consecutivo=vid, cliente_id=None, vendedor_id=vendedor_id,
        fecha=datetime(2026, 6, 4, 10, 0, 0), subtotal=Decimal("10000.00"),
        impuestos=Decimal("0.00"), total=Decimal("10000.00"), metodo_pago="efectivo",
        estado="completada", origen="web", idempotency_key=None,
    )


_LINEA = VentaDetalleLeer(
    producto_id=1, descripcion="Martillo", cantidad=Decimal("2"),
    precio_unitario=Decimal("5000.00"), iva=19,
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
        v = self._por_id.get(venta_id)
        if v is None:
            return None
        return VentaConLineas(**v.model_dump(), lineas=[_LINEA])


class _FakeFacturacionRepo:
    """Fake del repo de facturación: cuenta los batches (para verificar que NO hay N+1) y los ids pedidos."""

    def __init__(self, estados: dict[int, EstadoFiscalVenta] | None = None) -> None:
        self._estados = estados or {}
        self.llamadas = 0
        self.ids_pedidos: list[int] | None = None

    async def estados_por_ventas(self, venta_ids: list[int]) -> dict[int, EstadoFiscalVenta]:
        self.llamadas += 1
        self.ids_pedidos = list(venta_ids)
        return {vid: self._estados[vid] for vid in venta_ids if vid in self._estados}


def _app(
    repo: _FakeVentasRepo, *, rol: str = "vendedor", user_id: int = 5,
    capacidades: frozenset[str] = frozenset({"pos"}), fact_repo: _FakeFacturacionRepo | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_ventas_repo] = lambda: repo
    app.dependency_overrides[get_facturacion_lectura] = lambda: fact_repo or _FakeFacturacionRepo()
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: capacidades  # router POS (ADR 0008)
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
    body = ok.json()
    assert body["id"] == 1
    assert body["lineas"][0]["descripcion"] == "Martillo"   # el detalle trae las líneas
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


# --- F2.3c: composición del estado fiscal (badge) sobre la lista/detalle ------

_CAP_FISCAL = frozenset({"pos", "pos_electronico"})   # tenant con capacidad fiscal (POS-default)


def _estado(**over) -> EstadoFiscalVenta:
    base = {"tipo": "pos", "estado": "aceptada", "cufe": "CUDE-1", "numero": 7, "prefijo": "DPOS"}
    return EstadoFiscalVenta(**{**base, **over})


async def test_lista_compone_estado_fiscal_pos_aceptado():
    """Venta con POS aceptado en un tenant con capacidad fiscal → `fiscal` poblado (tipo/estado/cufe)."""
    repo = _FakeVentasRepo([_venta(1)])
    fact = _FakeFacturacionRepo({1: _estado()})
    app = _app(repo, capacidades=_CAP_FISCAL, fact_repo=fact)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas")
    assert r.status_code == 200, r.text
    assert r.json()[0]["fiscal"] == {
        "tipo": "pos", "estado": "aceptada", "cufe": "CUDE-1", "numero": 7, "prefijo": "DPOS",
    }
    assert fact.llamadas == 1                              # un solo batch


async def test_lista_venta_sin_documento_fiscal_none():
    """Tenant con capacidad fiscal pero la venta no generó documento → `fiscal=None`."""
    repo = _FakeVentasRepo([_venta(1)])
    fact = _FakeFacturacionRepo({})                       # sin documento para la venta 1
    app = _app(repo, capacidades=_CAP_FISCAL, fact_repo=fact)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas")
    assert r.json()[0]["fiscal"] is None
    assert fact.llamadas == 1


async def test_sin_capacidad_fiscal_no_consulta_la_tabla():
    """Tenant sin capacidad fiscal (solo `pos`) → `fiscal=None` SIN tocar facturas_electronicas."""
    repo = _FakeVentasRepo([_venta(1)])
    fact = _FakeFacturacionRepo({1: _estado()})           # habría documento, pero no se debe consultar
    app = _app(repo, capacidades=frozenset({"pos"}), fact_repo=fact)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas")
    assert r.json()[0]["fiscal"] is None
    assert fact.llamadas == 0                              # NO se consultó la tabla


async def test_lista_batch_sin_n_mas_uno():
    """Lista de 3 ventas → UNA sola query batch con todos los ids (sin N+1)."""
    repo = _FakeVentasRepo([_venta(1), _venta(2), _venta(3)])
    fact = _FakeFacturacionRepo({2: _estado(tipo="factura", estado="pendiente", cufe=None, numero=None, prefijo="FPR")})
    app = _app(repo, capacidades=_CAP_FISCAL, fact_repo=fact)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas")
    body = r.json()
    assert fact.llamadas == 1                              # una sola llamada para las 3 ventas
    assert sorted(fact.ids_pedidos) == [1, 2, 3]           # pidió todas en el batch
    assert body[0]["fiscal"] is None
    assert body[1]["fiscal"]["estado"] == "pendiente" and body[1]["fiscal"]["tipo"] == "factura"
    assert body[2]["fiscal"] is None


async def test_detalle_compone_estado_fiscal():
    """GET /ventas/{id} también lleva el estado fiscal (para el CUDE/CUFE del detalle)."""
    repo = _FakeVentasRepo([_venta(1)])
    fact = _FakeFacturacionRepo({1: _estado(cufe="CUDE-XYZ")})
    app = _app(repo, capacidades=_CAP_FISCAL, fact_repo=fact)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fiscal"]["cufe"] == "CUDE-XYZ" and body["lineas"][0]["descripcion"] == "Martillo"
