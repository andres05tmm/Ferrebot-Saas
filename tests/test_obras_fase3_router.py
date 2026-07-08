"""Router de la Fase 3 de obras (gasto real, consumo, liquidación): wiring HTTP con servicio falso.

Patrón de test_obras_router: app mínima + ASGITransport + dependency_overrides, servicio FAKE (cero red,
cero Postgres). Cubre el mapeo de errores a HTTP (404/409), el gate de rol (financiero = admin) y la forma
de las respuestas. La lógica real vive en los tests de integración (test_obras_gasto_real / _liquidacion /
_consumo_inventario)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.inventario.errors import AjusteDejaStockNegativo, ProductoInexistente
from modules.obra.errors import ConsumoEnObraLiquidada, ObraInexistente, ObraNoFinalizada
from modules.obra.router import get_obras_service, router
from modules.obra.service import GastoRealResultado
from services.calculations.obra import DesgloseGasto, Semaforo


def _desglose() -> DesgloseGasto:
    return DesgloseGasto(
        total_gastos=Decimal("1000000.00"), total_compras=Decimal("2000000.00"),
        total_prorrateo_nomina=Decimal("500000.00"), total_horas_maquina=Decimal("500000.00"),
        total_consumos_inventario=Decimal("100000.00"), total=Decimal("4100000.00"),
        semaforo=Semaforo.VERDE,
    )


def _liquidacion(oid: int = 1):
    return SimpleNamespace(
        id=9, obra_id=oid, fecha_liquidacion=datetime(2026, 7, 6, 12, 0, 0),
        ingreso_presupuestado=Decimal("11200000.00"), utilidad_presupuestada=Decimal("400000.00"),
        gasto_total=Decimal("4100000.00"), total_gastos=Decimal("1000000.00"),
        total_compras=Decimal("2000000.00"), total_prorrateo_nomina=Decimal("500000.00"),
        total_horas_maquina=Decimal("500000.00"), total_consumos_inventario=Decimal("100000.00"),
        utilidad_real=Decimal("7100000.00"), semaforo="verde", snapshot_json={"version": 1},
        creado_en=datetime(2026, 7, 6, 12, 0, 0),
    )


class _FakeObras:
    """Imita los métodos de Fase 3 del ObrasService; parametrizable para forzar cada rama de error."""

    def __init__(self, *, existe=True, finalizada=True, consumo_error=None) -> None:
        self._existe = existe
        self._finalizada = finalizada
        self._consumo_error = consumo_error

    async def gasto_real(self, obra_id):
        if not self._existe:
            raise ObraInexistente(obra_id)
        return GastoRealResultado(
            obra_id=obra_id, ingreso_presupuestado=Decimal("11200000.00"),
            utilidad_presupuestada=Decimal("400000.00"), tiene_presupuesto=True,
            desglose=_desglose(), utilidad_real=Decimal("7100000.00"), alerta_margen=False,
        )

    async def registrar_consumo(self, obra_id, datos, *, usuario_id=None):
        if not self._existe:
            raise ObraInexistente(obra_id)
        if self._consumo_error is not None:
            raise self._consumo_error
        consumo = SimpleNamespace(
            id=5, producto_id=datos.producto_id, obra_id=obra_id, fecha=date(2026, 7, 6),
            cantidad=datos.cantidad, costo_unitario=Decimal("28000.00"),
            responsable=datos.responsable, observaciones=datos.observaciones,
            creado_en=datetime(2026, 7, 6, 12, 0, 0),
        )
        resultado = SimpleNamespace(movimiento_id=77, stock_actual=Decimal("70.000"))
        return consumo, resultado

    async def liquidar(self, obra_id):
        if not self._existe:
            raise ObraInexistente(obra_id)
        if not self._finalizada:
            raise ObraNoFinalizada(obra_id, "EN_EJECUCION")
        return _liquidacion(obra_id)

    async def obtener_liquidacion(self, obra_id):
        if not self._existe:
            raise ObraInexistente(obra_id)
        return _liquidacion(obra_id)


def _app(service, *, rol="admin", caps=frozenset({"obras"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_obras_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def test_gasto_real_200_y_desglose():
    async with _cliente(_app(_FakeObras())) as c:
        r = await c.get("/api/v1/obras/1/gasto-real")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gasto_total"] == "4100000.00"
    assert body["semaforo"] == "verde"
    assert body["utilidad_real"] == "7100000.00"
    assert body["tiene_presupuesto"] is True


async def test_gasto_real_404():
    async with _cliente(_app(_FakeObras(existe=False))) as c:
        r = await c.get("/api/v1/obras/9/gasto-real")
    assert r.status_code == 404


async def test_gasto_real_requiere_admin():
    """Vista financiera: un vendedor no puede consultarla (403)."""
    async with _cliente(_app(_FakeObras(), rol="vendedor")) as c:
        r = await c.get("/api/v1/obras/1/gasto-real")
    assert r.status_code == 403, r.text


async def test_consumo_201_con_movimiento():
    async with _cliente(_app(_FakeObras(), rol="vendedor")) as c:   # consumo es de campo (vendedor)
        r = await c.post("/api/v1/obras/1/consumos", json={"producto_id": 3, "cantidad": "30"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["movimiento_id"] == 77
    assert body["stock_resultante"] == "70.000"
    assert body["producto_id"] == 3


async def test_consumo_producto_inexistente_404():
    svc = _FakeObras(consumo_error=ProductoInexistente(3))
    async with _cliente(_app(svc, rol="vendedor")) as c:
        r = await c.post("/api/v1/obras/1/consumos", json={"producto_id": 3, "cantidad": "1"})
    assert r.status_code == 404, r.text


async def test_consumo_stock_negativo_409():
    svc = _FakeObras(consumo_error=AjusteDejaStockNegativo(3, Decimal("5"), Decimal("-10")))
    async with _cliente(_app(svc, rol="vendedor")) as c:
        r = await c.post("/api/v1/obras/1/consumos", json={"producto_id": 3, "cantidad": "10"})
    assert r.status_code == 409, r.text


async def test_consumo_obra_liquidada_409():
    svc = _FakeObras(consumo_error=ConsumoEnObraLiquidada(1))
    async with _cliente(_app(svc, rol="vendedor")) as c:
        r = await c.post("/api/v1/obras/1/consumos", json={"producto_id": 3, "cantidad": "1"})
    assert r.status_code == 409, r.text


async def test_liquidar_200_y_409_si_no_finalizada():
    async with _cliente(_app(_FakeObras())) as c:
        ok = await c.post("/api/v1/obras/1/liquidar")
    async with _cliente(_app(_FakeObras(finalizada=False))) as c:
        mala = await c.post("/api/v1/obras/1/liquidar")
    assert ok.status_code == 200 and ok.json()["semaforo"] == "verde"
    assert ok.json()["snapshot_json"] == {"version": 1}
    assert mala.status_code == 409, mala.text


async def test_liquidacion_get_404_si_no_existe():
    async with _cliente(_app(_FakeObras(existe=False))) as c:
        r = await c.get("/api/v1/obras/9/liquidacion")
    assert r.status_code == 404


async def test_gate_obras_oculta_endpoints_fase3():
    async with _cliente(_app(_FakeObras(), caps=frozenset())) as c:
        r = await c.get("/api/v1/obras/1/gasto-real")
    assert r.status_code == 404, r.text
