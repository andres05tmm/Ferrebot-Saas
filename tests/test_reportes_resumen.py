"""E2 Parte 3 — GET /reportes/resumen (resumen del día, B4).

Patrón test_facturacion_router: app mínima + ASGITransport + dependency_overrides. Se inyecta un
fake del REPO (devuelve el agregado del día) para ejercitar el `ReportesService` REAL: cálculo del
ticket promedio y armado del resumen. El scoping lo decide el `get_filtro_efectivo` real (rol del
Principal). La exclusión de anuladas / agregación SQL sobre datos reales va en integración.
"""
from __future__ import annotations

from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.config.timezone import today_co
from modules.reportes.repository import AgregadoDia
from modules.reportes.router import get_reportes_repo, router


class _FakeReportesRepo:
    def __init__(self, agg: AgregadoDia) -> None:
        self._agg = agg
        self.vendedor_id: object = "UNSET"

    async def resumen(self, *, inicio, fin, vendedor_id):
        self.vendedor_id = vendedor_id
        return self._agg


def _app(repo: _FakeReportesRepo, *, rol: str = "vendedor", user_id: int = 5) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_reportes_repo] = lambda: repo
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_hoy_dashboard_vendedor_sin_utilidad_admin_con_ella():
    """RBAC del cockpit /hoy (F4): la utilidad estimada es un monto sensible del negocio completo —
    el vendedor recibe la MISMA respuesta pero con `utilidad_estimada` en None; el admin la ve."""
    from core.auth.features import get_capacidades
    from modules.reportes.repository import (
        AgregadoResultados,
        EstadoPedidosProveedor,
        InventarioConfiable,
        VencimientosCxP,
    )
    from modules.reportes.router import hoy_cache

    class _FakeHoyRepo:
        async def estado_resultados(self, *, inicio, fin):
            return AgregadoResultados(
                ingresos=Decimal("30000"), costo_ventas=Decimal("12000"), gastos=Decimal("3000"),
            )

        async def estado_pedidos_proveedor(self, *, hoy, ahora):
            return EstadoPedidosProveedor(en_camino=1, demorados=0, mas_viejo_horas=4.0)

        async def vencimientos_cxp(self, *, hoy):
            return VencimientosCxP(
                vencidas=0, monto_vencido=Decimal("0"),
                por_vencer_7d=0, monto_por_vencer=Decimal("0"),
            )

        async def total_fiado(self):
            return Decimal("10000")

        async def inventario_confiable(self):
            return InventarioConfiable(
                productos_activos=3, productos_cuadrados=1, stock_bajo_confiables=0,
            )

        async def caja_abierta_empresa(self):
            return True

    hoy_cache.clear()
    repo = _FakeHoyRepo()

    app_v = _app(repo, rol="vendedor", user_id=5)
    app_v.dependency_overrides[get_capacidades] = lambda: frozenset({"ventas"})
    async with _cliente(app_v) as c:
        r = await c.get("/api/v1/reportes/hoy-dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["utilidad_estimada"] is None                 # monto sensible: fuera para el vendedor
    assert body["fiados_total"] == "10000"
    assert body["caja_abierta"] is True

    app_a = _app(repo, rol="admin", user_id=1)
    app_a.dependency_overrides[get_capacidades] = lambda: frozenset({"ventas"})
    async with _cliente(app_a) as c:
        r = await c.get("/api/v1/reportes/hoy-dashboard")
    assert r.status_code == 200, r.text
    assert r.json()["utilidad_estimada"] == "15000"          # 30.000 − 12.000 − 3.000


async def test_resumen_agrega_y_calcula_ticket():
    agg = AgregadoDia(
        num_ventas=3, total_vendido=Decimal("30000.00"),
        por_metodo_pago={"efectivo": Decimal("20000.00"), "nequi": Decimal("10000.00")},
    )
    app = _app(_FakeReportesRepo(agg), rol="admin", user_id=1)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/resumen")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fecha"] == today_co().isoformat()
    assert body["num_ventas"] == 3
    assert body["total_vendido"] == "30000.00"
    assert body["ticket_promedio"] == "10000.00"            # 30000 / 3
    assert body["por_metodo_pago"] == {"efectivo": "20000.00", "nequi": "10000.00"}


async def test_dia_sin_ventas_da_ceros():
    agg = AgregadoDia(num_ventas=0, total_vendido=Decimal("0"), por_metodo_pago={})
    app = _app(_FakeReportesRepo(agg), rol="admin", user_id=1)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/reportes/resumen")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["num_ventas"] == 0
    assert body["total_vendido"] == "0"
    assert body["ticket_promedio"] == "0"                   # sin división por cero
    assert body["por_metodo_pago"] == {}


async def test_scoping_vendedor_y_admin():
    agg = AgregadoDia(num_ventas=0, total_vendido=Decimal("0"), por_metodo_pago={})

    repo_v = _FakeReportesRepo(agg)
    app_v = _app(repo_v, rol="vendedor", user_id=5)
    async with _cliente(app_v) as c:
        await c.get("/api/v1/reportes/resumen", params={"vendedor_id": 99})
    assert repo_v.vendedor_id == 5                           # vendedor: solo lo suyo

    repo_a = _FakeReportesRepo(agg)
    app_a = _app(repo_a, rol="admin", user_id=1)
    async with _cliente(app_a) as c:
        await c.get("/api/v1/reportes/resumen")
    assert repo_a.vendedor_id is None                        # admin: todo

    repo_i = _FakeReportesRepo(agg)
    app_i = _app(repo_i, rol="admin", user_id=1)
    async with _cliente(app_i) as c:
        await c.get("/api/v1/reportes/resumen", params={"vendedor_id": 7})
    assert repo_i.vendedor_id == 7                           # admin impersona
