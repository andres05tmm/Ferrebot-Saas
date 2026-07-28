"""`GET /ventas/historial` y su export: RBAC, cota del rango y colisión de rutas.

Patrón de `test_ventas_listar.py`: app mínima + repos fake; el scoping lo decide el
`get_filtro_efectivo` REAL según el rol del Principal. El SQL se cubre en `test_ventas_historial.py`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import today_co
from modules.ventas.router import (
    RANGO_HISTORIAL_MAX_DIAS,
    get_reportes_lectura,
    get_ventas_repo,
    router,
)
from modules.ventas.schemas import HistorialFeed, HistorialLinea


def _linea(venta_id: int = 1) -> HistorialLinea:
    return HistorialLinea(
        linea_id=venta_id, venta_id=venta_id, consecutivo=venta_id,
        fecha=datetime(2026, 7, 27, 14, 30), estado="completada",
        producto="Cemento gris 50kg", producto_id=7, cantidad=Decimal("2"),
        precio_unitario=Decimal("20000"), iva=0, total_linea=Decimal("40000"),
        cliente="Consumidor Final", cliente_id=None, vendedor="Andrés", vendedor_id=5,
        metodo_pago="efectivo", pagos=[], venta_total=Decimal("40000"), num_lineas=1,
    )


class _FakeRepo:
    def __init__(self, *, hay_mas: bool = False) -> None:
        self.args: dict | None = None
        self.llamadas = 0
        self._hay_mas = hay_mas

    async def historial_lineas(self, *, desde, hasta, vendedor_id=None, limite=100, offset=0):
        self.llamadas += 1
        self.args = {"desde": desde, "hasta": hasta, "vendedor_id": vendedor_id,
                     "limite": limite, "offset": offset}
        return HistorialFeed(desde=desde, hasta=hasta, filas=[_linea()], hay_mas=self._hay_mas)


class _FakeReportes:
    def __init__(self) -> None:
        self.args: dict | None = None

    async def resumen(self, *, inicio, fin, vendedor_id=None):
        self.args = {"inicio": inicio, "fin": fin, "vendedor_id": vendedor_id}
        return type("Agg", (), {"total_vendido": Decimal("40000"), "num_ventas": 1,
                                "por_metodo_pago": {}})()


def _app(repo: _FakeRepo, *, rol: str = "vendedor", user_id: int = 5,
         reportes: _FakeReportes | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_ventas_repo] = lambda: repo
    app.dependency_overrides[get_reportes_lectura] = lambda: reportes or _FakeReportes()
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: frozenset({"pos"})
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                             base_url="http://t")


async def test_historial_no_lo_traga_la_ruta_por_id():
    """`/ventas/historial` tiene que resolverse como historial, no como `venta_id='historial'`.
    Ya pasó con `/ventas/recientes`: si el orden de declaración se invierte, esto responde 422."""
    app = _app(_FakeRepo())
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial")
    assert r.status_code == 200, r.text
    assert r.json()["filas"][0]["producto"] == "Cemento gris 50kg"


async def test_un_vendedor_no_se_baja_el_historial_de_la_tienda():
    repo = _FakeRepo()
    app = _app(repo, rol="vendedor", user_id=5)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial", params={"vendedor_id": 99})   # intenta impersonar
    assert r.status_code == 200, r.text
    assert repo.args["vendedor_id"] == 5


async def test_el_export_aplica_EL_MISMO_filtro_por_rol():
    """El endpoint donde se olvida el aislamiento: un vendedor bajándose el Excel de toda la tienda."""
    repo = _FakeRepo()
    app = _app(repo, rol="vendedor", user_id=5)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial/exportar", params={"vendedor_id": 99})
    assert r.status_code == 200, r.text
    assert repo.args["vendedor_id"] == 5


async def test_el_admin_ve_todo_y_puede_impersonar():
    repo = _FakeRepo()
    app = _app(repo, rol="admin", user_id=1)
    async with _cliente(app) as c:
        await c.get("/api/v1/ventas/historial")
        assert repo.args["vendedor_id"] is None
        await c.get("/api/v1/ventas/historial", params={"vendedor_id": 7})
        assert repo.args["vendedor_id"] == 7


async def test_un_rango_gigante_se_rechaza_sin_tocar_la_base():
    """La cota existe para que `?desde=2020-01-01` no recorra `ventas` unida a `ventas_detalle`.
    Si el repo llegara a llamarse, la cota no estaría sirviendo de nada."""
    repo = _FakeRepo()
    app = _app(repo)
    hoy = today_co()
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial", params={
            "desde": str(hoy - timedelta(days=RANGO_HISTORIAL_MAX_DIAS + 1)), "hasta": str(hoy)})
    assert r.status_code == 422
    assert repo.llamadas == 0


async def test_la_misma_cota_protege_al_export():
    """La cota vive en una dependencia compartida justamente para que el export no se la salte."""
    repo = _FakeRepo()
    app = _app(repo)
    hoy = today_co()
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial/exportar", params={
            "desde": str(hoy - timedelta(days=365)), "hasta": str(hoy)})
    assert r.status_code == 422
    assert repo.llamadas == 0


async def test_fecha_final_anterior_a_la_inicial_se_rechaza():
    app = _app(_FakeRepo())
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial",
                        params={"desde": "2026-07-27", "hasta": "2026-07-01"})
    assert r.status_code == 422


async def test_sin_fechas_el_rango_es_hoy_colombia():
    repo = _FakeRepo()
    app = _app(repo)
    async with _cliente(app) as c:
        await c.get("/api/v1/ventas/historial")
    assert repo.args["desde"] == today_co() == repo.args["hasta"]


async def test_el_export_devuelve_un_xlsx_descargable():
    app = _app(_FakeRepo())
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial/exportar",
                        params={"desde": "2026-07-27", "hasta": "2026-07-27"})
    assert r.status_code == 200, r.text
    assert "spreadsheetml" in r.headers["content-type"]
    assert 'filename="ventas_2026-07-27_2026-07-27.xlsx"' in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"          # un .xlsx es un zip


async def test_un_export_que_no_cabe_falla_en_vez_de_entregar_medio_archivo():
    """Un Excel con la mitad de las ventas del mes es peor que un error: el que lo abre no tiene
    cómo notar lo que falta, y lo va a usar para cuadrar plata."""
    app = _app(_FakeRepo(hay_mas=True))
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial/exportar",
                        params={"desde": "2026-07-01", "hasta": "2026-07-27"})
    assert r.status_code == 422
    assert "meses" in r.json()["detail"]


async def test_el_total_del_pie_sale_de_reportes_no_de_las_lineas():
    """El pie del Excel se pide al agregador de cabeceras (que excluye anuladas), con el mismo
    filtro por rol y el mismo rango que el feed."""
    reportes = _FakeReportes()
    app = _app(_FakeRepo(), rol="vendedor", user_id=5, reportes=reportes)
    async with _cliente(app) as c:
        r = await c.get("/api/v1/ventas/historial/exportar",
                        params={"desde": "2026-07-27", "hasta": "2026-07-27"})
    assert r.status_code == 200, r.text
    assert reportes.args["vendedor_id"] == 5
    assert reportes.args["inicio"].date() == date(2026, 7, 27)
