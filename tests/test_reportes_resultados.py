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


def _agg(
    *,
    ventas_brutas="0", devoluciones="0", costo_ventas="0", gastos="0",
    unidades_vendidas="0", unidades_sin_costo="0",
) -> AgregadoResultados:
    """Agregado crudo del repo, con todo en cero salvo lo que el test declara."""
    return AgregadoResultados(
        ventas_brutas=Decimal(ventas_brutas), devoluciones=Decimal(devoluciones),
        costo_ventas=Decimal(costo_ventas), gastos=Decimal(gastos),
        unidades_vendidas=Decimal(unidades_vendidas),
        unidades_sin_costo=Decimal(unidades_sin_costo),
    )


class _FakeReportesRepo:
    def __init__(
        self, *, resultados: AgregadoResultados | None = None, anterior=None, top=None
    ) -> None:
        self._resultados = resultados
        # El servicio pide el rango y LUEGO la ventana anterior: se sirven en ese orden.
        self._anterior = anterior if anterior is not None else _agg()
        self._top = top or []
        self.ventanas: list[tuple] = []
        self.vendedor_id: object = "UNSET"
        self.limite: object = "UNSET"

    async def estado_resultados(self, *, inicio, fin) -> AgregadoResultados:
        self.ventanas.append((inicio, fin))
        return self._resultados if len(self.ventanas) == 1 else self._anterior

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
    agg = _agg(ventas_brutas="100000.00", costo_ventas="60000.00", gastos="15000.00")
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


async def test_resultados_devolucion_total_deja_utilidad_en_cero():
    """El bug que esto cierra: la devolución revertía el COSTO pero no el INGRESO, así que devolver
    mercancía SUBÍA el margen. Venta de 100k devuelta entera ⇒ no vendiste nada."""
    agg = _agg(ventas_brutas="100000.00", devoluciones="100000.00", costo_ventas="0.00")
    app = _app(_FakeReportesRepo(resultados=agg), rol="admin")
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/resultados")
    body = r.json()
    assert body["ventas_brutas"] == "100000.00"
    assert body["devoluciones"] == "100000.00"
    assert body["ingresos"] == "0.00"
    assert body["utilidad_bruta"] == "0.00"


async def test_resultados_devolucion_parcial_no_mueve_el_margen_pct():
    """Devolver la mitad baja ingreso y costo en la misma proporción: el margen % no se mueve."""
    agg = _agg(ventas_brutas="100000.00", devoluciones="50000.00", costo_ventas="30000.00")
    app = _app(_FakeReportesRepo(resultados=agg), rol="admin")
    async with _cliente(app) as c:
        body = (await c.get("/api/v1/reportes/resultados")).json()
    ingresos, bruta = Decimal(body["ingresos"]), Decimal(body["utilidad_bruta"])
    assert ingresos == Decimal("50000.00")          # 100k − 50k
    assert bruta == Decimal("20000.00")             # 50k − 30k
    assert bruta / ingresos == Decimal("0.4")       # 40%, igual que 60k/(100k−40k) sin devolución


async def test_resultados_comparativo_es_la_ventana_anterior_del_mismo_largo():
    repo = _FakeReportesRepo(
        resultados=_agg(ventas_brutas="120000.00", gastos="20000.00"),
        anterior=_agg(ventas_brutas="100000.00", costo_ventas="60000.00", gastos="15000.00"),
    )
    app = _app(repo, rol="admin")
    async with _cliente(app) as c:
        body = (await c.get(
            "/api/v1/reportes/resultados", params={"desde": "2026-07-11", "hasta": "2026-07-20"}
        )).json()

    ant = body["anterior"]
    assert ant["desde"] == "2026-07-01" and ant["hasta"] == "2026-07-10"   # 10 días, sin solape
    assert ant["utilidad_neta"] == "25000.00"                              # 100k − 60k − 15k
    (_, fin_prev) = repo.ventanas[1]
    (ini_actual, _) = repo.ventanas[0]
    assert fin_prev < ini_actual                                           # pegado, sin traslape


async def test_resultados_comparativo_none_si_el_periodo_anterior_esta_vacio():
    """Sin movimiento previo no hay 'caída del 100%': hay ausencia de dato."""
    repo = _FakeReportesRepo(resultados=_agg(ventas_brutas="50000.00"), anterior=_agg())
    app = _app(repo, rol="admin")
    async with _cliente(app) as c:
        body = (await c.get("/api/v1/reportes/resultados")).json()
    assert body["anterior"] is None


async def test_resultados_cobertura_baja_con_unidades_sin_costo():
    agg = _agg(ventas_brutas="100000.00", unidades_vendidas="10", unidades_sin_costo="2")
    app = _app(_FakeReportesRepo(resultados=agg), rol="admin")
    async with _cliente(app) as c:
        body = (await c.get("/api/v1/reportes/resultados")).json()
    assert Decimal(body["cobertura_pct"]) == Decimal("80")     # 8 de 10 con costo snapshot


async def test_resultados_cobertura_100_sin_ventas():
    """Sin unidades vendidas no hay margen que dudar: 100 y no una división por cero."""
    app = _app(_FakeReportesRepo(resultados=_agg()), rol="admin")
    async with _cliente(app) as c:
        body = (await c.get("/api/v1/reportes/resultados")).json()
    assert Decimal(body["cobertura_pct"]) == Decimal("100")


async def test_resultados_es_admin_only_vendedor_403():
    app = _app(_FakeReportesRepo(resultados=_agg()), rol="vendedor", user_id=5)
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
