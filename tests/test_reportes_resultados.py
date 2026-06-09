"""Slice 2 — routers /reportes/resultados y /reportes/top-productos (router + servicio REAL, repo fake).

Patrón test_reportes_resumen: app mínima + ASGITransport + dependency_overrides. El fake del repo
devuelve los agregados crudos; se ejercita el ReportesService REAL (math de utilidades, defaults de
rango) y el control de rol/scoping real (require_role / get_filtro_efectivo). La agregación SQL va
en integración.
"""
from __future__ import annotations

from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.reportes.repository import AgregadoResultados, TopProductoFila
from modules.reportes.router import get_reportes_repo, router


class _FakeReportesRepo:
    def __init__(self, *, resultados: AgregadoResultados | None = None, top=None) -> None:
        self._resultados = resultados
        self._top = top or []
        self.vendedor_id: object = "UNSET"
        self.limite: object = "UNSET"

    async def estado_resultados(self, *, inicio, fin) -> AgregadoResultados:
        return self._resultados

    async def top_productos(self, *, inicio, fin, vendedor_id, limite):
        self.vendedor_id = vendedor_id
        self.limite = limite
        return self._top


def _app(repo: _FakeReportesRepo, *, rol: str = "admin", user_id: int = 1) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_reportes_repo] = lambda: repo
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    # /reportes/top-productos es POS (ADR 0008); el resto de reportes es núcleo. Damos `pos` al test.
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pos"})
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


# ---- Resultados ------------------------------------------------------------
async def test_resultados_calcula_utilidad_bruta_y_neta():
    agg = AgregadoResultados(
        ingresos=Decimal("100000.00"), costo_ventas=Decimal("60000.00"), gastos=Decimal("15000.00")
    )
    app = _app(_FakeReportesRepo(resultados=agg), rol="admin")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/resultados")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ingresos"] == "100000.00"
    assert body["costo_ventas"] == "60000.00"
    assert body["utilidad_bruta"] == "40000.00"     # 100000 − 60000
    assert body["gastos"] == "15000.00"
    assert body["utilidad_neta"] == "25000.00"      # 40000 − 15000
    assert body["desde"] and body["hasta"]          # rango por defecto (mes en curso) presente


async def test_resultados_es_admin_only_vendedor_403():
    agg = AgregadoResultados(ingresos=Decimal("0"), costo_ventas=Decimal("0"), gastos=Decimal("0"))
    app = _app(_FakeReportesRepo(resultados=agg), rol="vendedor", user_id=5)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/resultados")
    assert r.status_code == 403, r.text


# ---- Top productos ---------------------------------------------------------
async def test_top_productos_pinta_filas():
    top = [
        TopProductoFila(producto_id=1, nombre="Cemento", cantidad=Decimal("3"), ingreso=Decimal("30000")),
        TopProductoFila(producto_id=2, nombre="Arena", cantidad=Decimal("4"), ingreso=Decimal("20000")),
    ]
    app = _app(_FakeReportesRepo(top=top), rol="admin")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/top-productos")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["producto_id"] for f in body] == [1, 2]
    assert body[0]["nombre"] == "Cemento"
    assert body[0]["ingreso"] == "30000"


async def test_top_productos_respeta_scoping_rbac():
    fila = [TopProductoFila(producto_id=1, nombre="A", cantidad=Decimal("1"), ingreso=Decimal("1000"))]

    repo_v = _FakeReportesRepo(top=fila)
    app_v = _app(repo_v, rol="vendedor", user_id=5)
    async with _cliente(app_v) as c:
        await c.get("/api/v1/reportes/top-productos", params={"vendedor_id": 99})
    assert repo_v.vendedor_id == 5                    # vendedor: solo lo suyo (ignora ?vendedor_id)

    repo_a = _FakeReportesRepo(top=fila)
    app_a = _app(repo_a, rol="admin", user_id=1)
    async with _cliente(app_a) as c:
        await c.get("/api/v1/reportes/top-productos")
    assert repo_a.vendedor_id is None                 # admin: todo el negocio

    repo_i = _FakeReportesRepo(top=fila)
    app_i = _app(repo_i, rol="admin", user_id=1)
    async with _cliente(app_i) as c:
        await c.get("/api/v1/reportes/top-productos", params={"vendedor_id": 7, "limite": 5})
    assert repo_i.vendedor_id == 7                     # admin impersona
    assert repo_i.limite == 5
