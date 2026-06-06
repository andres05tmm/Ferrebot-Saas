"""Serie de ventas + totales (tab Hoy a paridad) — integración Postgres + router (repo fake).

La serie agrupa por día en hora Colombia y excluye anuladas; los totales (hoy/semana/mes) cuadran; y
ambos respetan el scope por vendedor. La parte de router verifica el cableado de `get_filtro_efectivo`
(vendedor ve lo suyo; admin ve todo o impersona) con un repo falso.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.config.timezone import COLOMBIA_TZ, today_co
from modules.reportes.repository import SqlReportesRepository
from modules.reportes.router import get_reportes_repo, router
from modules.reportes.service import ReportesService


def _dt(d):
    """Mediodía Colombia de la fecha `d` (aware), para que caiga en su día local sin ambigüedad."""
    return datetime.combine(d, time(12, 0), tzinfo=COLOMBIA_TZ)


async def _usuario(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES (:n,'vendedor') RETURNING id"), {"n": nombre}
        )
    ).scalar_one()


async def _venta(
    s: AsyncSession, *, consecutivo: int, vendedor_id: int, total: str, fecha, estado: str = "completada"
) -> None:
    await s.execute(
        text(
            "INSERT INTO ventas "
            "(consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago, estado, origen) "
            "VALUES (:c,:v,:f,:t,0,:t,'efectivo',:e,'web')"
        ),
        {"c": consecutivo, "v": vendedor_id, "f": _dt(fecha), "t": total, "e": estado},
    )


# ---- Integración (Postgres efímero) ----------------------------------------
async def test_serie_agrupa_por_dia_y_excluye_anuladas(tenant):
    hoy = today_co()
    ayer = hoy - timedelta(days=1)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        a = await _usuario(s, "Ana")
        await _venta(s, consecutivo=1, vendedor_id=a, total="20000", fecha=hoy)
        await _venta(s, consecutivo=2, vendedor_id=a, total="10000", fecha=hoy)
        await _venta(s, consecutivo=3, vendedor_id=a, total="5000", fecha=hoy, estado="anulada")
        await _venta(s, consecutivo=4, vendedor_id=a, total="8000", fecha=ayer)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        serie = await ReportesService(SqlReportesRepository(s)).serie_ventas(dias=7, vendedor_id=None)

    assert len(serie) == 7                                   # 7 días continuos
    por_fecha = {p.fecha: p.total for p in serie}
    assert por_fecha[hoy] == Decimal("30000")               # 20000 + 10000 (anulada fuera)
    assert por_fecha[ayer] == Decimal("8000")
    otros = [p.total for p in serie if p.fecha not in (hoy, ayer)]
    assert len(otros) == 5 and all(t == Decimal("0") for t in otros)   # días sin ventas → 0


async def test_totales_cuadran_hoy_semana_mes(tenant):
    hoy = today_co()
    hace6 = hoy - timedelta(days=6)   # borde de la semana (siempre dentro de los últimos 7 días)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        a = await _usuario(s, "Ana")
        await _venta(s, consecutivo=1, vendedor_id=a, total="10000", fecha=hoy)
        await _venta(s, consecutivo=2, vendedor_id=a, total="4000", fecha=hace6)
        await _venta(s, consecutivo=3, vendedor_id=a, total="9999", fecha=hoy, estado="anulada")
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        tot = await ReportesService(SqlReportesRepository(s)).totales(vendedor_id=None)

    assert tot.dia == Decimal("10000")                      # solo hoy, anulada excluida
    assert tot.semana == Decimal("14000")                   # hoy + hace 6 días (ambos en la semana)
    # `mes` incluye hace6 solo si cae en el mes en curso (depende del día del mes en que corra el test).
    mes_esperado = Decimal("14000") if hace6.month == hoy.month else Decimal("10000")
    assert tot.mes == mes_esperado


async def test_serie_y_totales_respetan_scope_por_vendedor(tenant):
    hoy = today_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        a = await _usuario(s, "Ana")
        b = await _usuario(s, "Beto")
        await _venta(s, consecutivo=1, vendedor_id=a, total="10000", fecha=hoy)
        await _venta(s, consecutivo=2, vendedor_id=b, total="5000", fecha=hoy)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        svc = ReportesService(SqlReportesRepository(s))
        tot_a = await svc.totales(vendedor_id=a)
        tot_all = await svc.totales(vendedor_id=None)
        serie_a = await svc.serie_ventas(dias=1, vendedor_id=a)

    assert tot_a.dia == Decimal("10000")                    # solo Ana
    assert tot_all.dia == Decimal("15000")                  # todo el negocio
    assert serie_a[-1].total == Decimal("10000")            # el día de hoy, scoped a Ana


async def test_sin_ventas_serie_en_cero_y_totales_cero(tenant):
    async with AsyncSession(tenant.engine) as s:
        svc = ReportesService(SqlReportesRepository(s))
        serie = await svc.serie_ventas(dias=5, vendedor_id=None)
        tot = await svc.totales(vendedor_id=None)

    assert len(serie) == 5 and all(p.total == Decimal("0") for p in serie)
    assert tot.dia == Decimal("0") and tot.semana == Decimal("0") and tot.mes == Decimal("0")


# ---- Router (repo fake): scope RBAC vía get_filtro_efectivo ----------------
class _FakeRepo:
    def __init__(self) -> None:
        self.serie_vendedor: object = "UNSET"
        self.total_vendedor: object = "UNSET"

    async def serie_ventas(self, *, inicio, fin, vendedor_id):
        self.serie_vendedor = vendedor_id
        return []

    async def total_ventas(self, *, inicio, fin, vendedor_id):
        self.total_vendedor = vendedor_id
        return Decimal("0")


def _app(repo: _FakeRepo, *, rol: str, user_id: int) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_reportes_repo] = lambda: repo
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=user_id, tenant="pr", rol=rol)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_router_scope_vendedor_ve_lo_suyo_admin_todo():
    # Vendedor: ignora ?vendedor_id, usa su propio id.
    repo_v = _FakeRepo()
    async with _cliente(_app(repo_v, rol="vendedor", user_id=5)) as c:
        r1 = await c.get("/api/v1/reportes/serie-ventas", params={"dias": 7, "vendedor_id": 99})
        r2 = await c.get("/api/v1/reportes/totales", params={"vendedor_id": 99})
    assert r1.status_code == 200 and r2.status_code == 200
    assert repo_v.serie_vendedor == 5 and repo_v.total_vendedor == 5

    # Admin sin ?vendedor_id: ve todo el negocio (None).
    repo_a = _FakeRepo()
    async with _cliente(_app(repo_a, rol="admin", user_id=1)) as c:
        await c.get("/api/v1/reportes/serie-ventas")
        await c.get("/api/v1/reportes/totales")
    assert repo_a.serie_vendedor is None and repo_a.total_vendedor is None

    # Admin con ?vendedor_id: impersona a ese vendedor.
    repo_i = _FakeRepo()
    async with _cliente(_app(repo_i, rol="admin", user_id=1)) as c:
        await c.get("/api/v1/reportes/totales", params={"vendedor_id": 7})
    assert repo_i.total_vendedor == 7
