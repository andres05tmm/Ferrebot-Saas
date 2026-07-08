"""Panel / home de obra (Fase 8): endpoint agregado y cacheado + LOW del mensaje de liquidación.

Dos capas: (1) wiring HTTP con servicio FAKE (rol admin, gate `obras`, forma de la respuesta y el 404 con
mensaje CORRECTO de la liquidación de una obra que existe pero no está liquidada); (2) integración real
contra Postgres efímero: el panel rollup + el conteo por estado + que las obras LIQUIDADAS no ensucian el
detalle en curso, y que el batch de agregados cuadra con el cálculo por-obra (sin N+1)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.obra.panel_cache import panel_cache
from modules.obra.repository import SqlObrasRepository
from modules.obra.router import get_obras_service, router
from modules.obra.service import ObrasService, PanelObra, PanelObraItem
from modules.obra.errors import ObraNoLiquidada, ObraInexistente


def _panel() -> PanelObra:
    return PanelObra(
        generado_en=datetime(2026, 7, 7, 12, 0, 0),
        total_obras=3, obras_activas=2,
        por_estado={"EN_EJECUCION": 1, "PLANIFICADA": 1, "LIQUIDADA": 1},
        ingreso_presupuestado_total=Decimal("10000000.00"),
        gasto_total=Decimal("4000000.00"),
        utilidad_real_total=Decimal("6000000.00"),
        obras_en_alerta=1,
        obras=[
            PanelObraItem(
                obra_id=1, nombre="Vía La Estrella", estado="EN_EJECUCION", cliente_id=2,
                ingreso_presupuestado=Decimal("10000000.00"), gasto_total=Decimal("4000000.00"),
                utilidad_real=Decimal("6000000.00"), tiene_presupuesto=True, semaforo="verde",
                alerta_margen=False,
            )
        ],
    )


class _FakeObras:
    def __init__(self, *, liquidada_existe: bool = True) -> None:
        self._liquidada_existe = liquidada_existe

    async def panel(self) -> PanelObra:
        return _panel()

    async def obtener_liquidacion(self, obra_id):
        # La obra EXISTE pero no está liquidada → error dedicado (mensaje correcto), no ObraInexistente.
        if not self._liquidada_existe:
            raise ObraNoLiquidada(obra_id)
        raise ObraInexistente(obra_id)


def _app(service, *, rol="admin", caps=frozenset({"obras"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_obras_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


# ---- Router (servicio fake) ----------------------------------------------------
async def test_panel_200_forma_y_rollup():
    async with _cliente(_app(_FakeObras())) as c:
        r = await c.get("/api/v1/obras/panel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_obras"] == 3
    assert body["obras_activas"] == 2
    assert body["por_estado"]["LIQUIDADA"] == 1
    assert body["gasto_total"] == "4000000.00"
    assert body["obras_en_alerta"] == 1
    assert len(body["obras"]) == 1
    assert body["obras"][0]["semaforo"] == "verde"


async def test_panel_requiere_admin():
    """Vista financiera del portafolio: un vendedor no la ve (403)."""
    async with _cliente(_app(_FakeObras(), rol="vendedor")) as c:
        r = await c.get("/api/v1/obras/panel")
    assert r.status_code == 403, r.text


async def test_panel_gateado_por_obras():
    async with _cliente(_app(_FakeObras(), caps=frozenset())) as c:
        r = await c.get("/api/v1/obras/panel")
    assert r.status_code == 404, r.text


async def test_liquidacion_de_obra_no_liquidada_404_mensaje_correcto():
    """LOW: GET liquidación de una obra que EXISTE pero no está liquidada → 404 con mensaje que NO dice
    'la obra no existe' (antes reusaba ObraInexistente y engañaba)."""
    async with _cliente(_app(_FakeObras(liquidada_existe=False))) as c:
        r = await c.get("/api/v1/obras/9/liquidacion")
    assert r.status_code == 404, r.text
    detalle = r.json()["detail"]
    assert "no existe" not in detalle
    assert "aún no está liquidada" in detalle


# ---- Integración real (Postgres efímero) --------------------------------------
async def _seed_cliente(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Alcaldía', 0) RETURNING id"))
    ).scalar_one()


async def _seed_obra(s: AsyncSession, *, cliente_id: int, nombre: str, estado: str) -> int:
    return (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c, :n, :e) RETURNING id"),
            {"c": cliente_id, "n": nombre, "e": estado},
        )
    ).scalar_one()


async def test_panel_integracion_rollup_conteo_y_excluye_liquidada(tenant):
    panel_cache.clear()
    async with AsyncSession(tenant.engine) as s:
        cid = await _seed_cliente(s)
        o1 = await _seed_obra(s, cliente_id=cid, nombre="En ejecución", estado="EN_EJECUCION")
        await _seed_obra(s, cliente_id=cid, nombre="Planificada", estado="PLANIFICADA")
        o3 = await _seed_obra(s, cliente_id=cid, nombre="Cerrada", estado="LIQUIDADA")
        # Gasto imputado a la obra en ejecución (sin cotización → sin presupuesto).
        await s.execute(
            text("INSERT INTO gastos (categoria, monto, obra_id) VALUES ('otros', 500000, :o)"),
            {"o": o1},
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        panel = await ObrasService(SqlObrasRepository(s)).panel()

    assert panel.total_obras == 3
    assert panel.obras_activas == 2                       # la LIQUIDADA no cuenta como activa
    assert panel.por_estado["LIQUIDADA"] == 1             # pero sí en el conteo del portafolio
    ids = {it.obra_id for it in panel.obras}
    assert o3 not in ids                                  # liquidada excluida del detalle en curso
    item1 = next(it for it in panel.obras if it.obra_id == o1)
    assert item1.gasto_total == Decimal("500000")         # el gasto imputado
    assert item1.tiene_presupuesto is False
    assert item1.semaforo == "rojo"                       # sin presupuesto → semáforo rojo
    assert panel.gasto_total == Decimal("500000")         # rollup solo de las activas


async def test_agregados_batch_cuadra_con_por_obra(tenant):
    """El batch (una consulta agrupada por obra) debe dar EXACTAMENTE lo mismo que el cálculo por-obra."""
    async with AsyncSession(tenant.engine) as s:
        cid = await _seed_cliente(s)
        o1 = await _seed_obra(s, cliente_id=cid, nombre="A", estado="EN_EJECUCION")
        o2 = await _seed_obra(s, cliente_id=cid, nombre="B", estado="PLANIFICADA")
        await s.execute(text("INSERT INTO gastos (categoria, monto, obra_id) VALUES ('otros', 300000, :o)"), {"o": o1})
        await s.execute(text("INSERT INTO gastos (categoria, monto, obra_id) VALUES ('otros', 120000, :o)"), {"o": o1})
        await s.execute(text("INSERT INTO gastos (categoria, monto, obra_id) VALUES ('otros', 90000, :o)"), {"o": o2})
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        repo = SqlObrasRepository(s)
        batch = await repo.agregados_gasto_batch([o1, o2])
        uno = await repo.agregados_gasto(o1)
        dos = await repo.agregados_gasto(o2)
    assert batch[o1] == uno
    assert batch[o2] == dos
    assert batch[o1].total_gastos == Decimal("420000")
    assert batch[o2].total_gastos == Decimal("90000")
