"""Cockpit del vertical construcción (GET /obras/dashboard, Fase 2) — doble capa.

(0) AISLAMIENTO multi-tenant (invariante crítico, TEST-PRIMERO): el dashboard de la empresa A jamás ve
    datos de la B (la base ES la frontera; el cockpit agrega SOLO lo de su tenant).
(1) Wiring HTTP con compositor FAKE (patrón `test_obras_panel.py`): forma de la respuesta, 403 del
    vendedor, gate `obras`, y la CACHÉ (2º hit servido desde memoria, con `clear()` por fixture).
(2) Integración real contra Postgres efímero: los KPIs del mes (ingreso alquiler por LATERAL, resbalos/
    compras/gastos), el tablero de máquinas (ocupadas hoy, top del mes), conteos y alertas.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import today_co
from modules.obra.dashboard import DashboardConstruccionService
from modules.obra.panel_cache import dashboard_cache
from modules.obra.router import get_dashboard_service, router
from modules.obra.schemas import (
    AlertaDashboard,
    ConteosDashboard,
    DashboardConstruccion,
    KpisMes,
    KpisMesAnterior,
    MaquinaOcupadaHoy,
    MaquinasDashboard,
    MesRango,
    ObraPanel,
    TopMaquinaMes,
)

# Los repos del compositor cruzan FKs entre módulos (gastos/maquinas/obras/clientes): registrarlos en la
# metadata del ORM al correr este archivo en aislamiento.
import modules.obra.models  # noqa: E402,F401
import modules.maquinaria.models  # noqa: E402,F401
import modules.caja.models  # noqa: E402,F401
import modules.compras.models  # noqa: E402,F401


# =====================================================================================================
# (0) AISLAMIENTO multi-tenant — invariante crítico, escrito PRIMERO
# =====================================================================================================
async def _seed_ingreso(engine, *, horas: str = "6", precio: str = "100000") -> None:
    """Siembra máquina OCUPADA + obra + asignación activa hoy + un parte de horas de HOY (genera ingreso)."""
    async with AsyncSession(engine) as s:
        cid = (
            await s.execute(text("INSERT INTO clientes (nombre) VALUES ('Alcaldía') RETURNING id"))
        ).scalar_one()
        oid = (
            await s.execute(
                text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c,'Vía','EN_EJECUCION') RETURNING id"),
                {"c": cid},
            )
        ).scalar_one()
        mid = (
            await s.execute(
                text(
                    "INSERT INTO maquinas (codigo,nombre,tipo,precio_hora_default,estado) "
                    "VALUES ('M-1','Vibro','vibro',150000,'OCUPADA') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO asignaciones_maquina_obra "
                "(maquina_id,obra_id,fecha_inicio,precio_hora,minimo_horas,activa) "
                "VALUES (:m,:o,:ini,:p,1,true)"
            ),
            {"m": mid, "o": oid, "ini": today_co().replace(day=1), "p": precio},
        )
        await s.execute(
            text(
                "INSERT INTO registros_horas_maquina (maquina_id,obra_id,fecha,horas_trabajadas,horas_facturables) "
                "VALUES (:m,:o,:f,:h,:h)"
            ),
            {"m": mid, "o": oid, "f": today_co(), "h": horas},
        )
        await s.commit()


async def test_dashboard_A_no_ve_datos_de_B(tenant_factory):
    """El cockpit de A ve su ingreso/máquina; el de B (vacío) ve ceros — sin cruce entre empresas."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()
    await _seed_ingreso(empresa_a.engine)

    caps = frozenset({"obras"})
    async with AsyncSession(empresa_a.engine) as sa:
        dash_a = await DashboardConstruccionService(sa, caps).construir()
    async with AsyncSession(empresa_b.engine) as sb:
        dash_b = await DashboardConstruccionService(sb, caps).construir()

    assert dash_a.kpis_mes.ingreso_alquiler == Decimal("600000")   # 6 h × 100000
    assert dash_a.maquinas.total == 1
    assert dash_b.kpis_mes.ingreso_alquiler == Decimal("0")        # B no ve nada de A
    assert dash_b.maquinas.total == 0
    assert dash_b.portafolio.total_obras == 0


# =====================================================================================================
# (1) Wiring HTTP con compositor FAKE
# =====================================================================================================
def _dashboard() -> DashboardConstruccion:
    return DashboardConstruccion(
        generado_en="2026-07-08T12:00:00-05:00",
        mes=MesRango(desde=date(2026, 7, 1), hasta=date(2026, 7, 31)),
        kpis_mes=KpisMes(
            ingreso_alquiler=Decimal("600000.00"), resbalos=Decimal("50000.00"),
            ingreso_total=Decimal("650000.00"), gastos=Decimal("230000.00"),
            compras=Decimal("150000.00"), gasto_total=Decimal("380000.00"),
            utilidad_estimada=Decimal("270000.00"), margen_pct=Decimal("41.54"),
            semaforo_utilidad="verde", flujo_caja_neto=Decimal("270000.00"),
            mes_anterior=KpisMesAnterior(ingreso_total=Decimal("0.00"), gasto_total=Decimal("0.00")),
        ),
        portafolio=ObraPanel(
            generado_en="2026-07-08T12:00:00-05:00", total_obras=1, obras_activas=1,
            por_estado={"EN_EJECUCION": 1}, ingreso_presupuestado_total=Decimal("0.00"),
            gasto_total=Decimal("0.00"), utilidad_real_total=Decimal("0.00"), obras_en_alerta=0, obras=[],
        ),
        maquinas=MaquinasDashboard(
            total=1, por_estado={"OCUPADA": 1},
            ocupadas_hoy=[MaquinaOcupadaHoy(
                maquina_id=1, maquina="Vibro", obra_nombre="Vía", operador_nombre="Juan Pérez",
                horas_hoy=Decimal("6"), ingreso_hoy=Decimal("600000.00"),
            )],
            top_mes=[TopMaquinaMes(maquina_id=1, maquina="Vibro", horas=Decimal("6"), ingreso=Decimal("600000.00"))],
        ),
        alertas=[AlertaDashboard(
            tipo="mantenimiento_vencido", severidad="rojo", titulo="Mantenimiento vencido: Vibro",
            detalle="programado para 2026-07-01", ref_id=1, ruta="/maquinas",
        )],
        conteos=ConteosDashboard(gastos_por_revisar=1, colitas=0, cotizaciones_por_vencer=1),
    )


class _FakeDashboard:
    def __init__(self) -> None:
        self.llamadas = 0

    async def construir(self) -> DashboardConstruccion:
        self.llamadas += 1
        return _dashboard()


def _app(service, *, rol="admin", caps=frozenset({"obras"}), empresa_id=None) -> FastAPI:
    app = FastAPI()
    if empresa_id is not None:
        @app.middleware("http")
        async def _set_tenant(request, call_next):   # resuelve un tenant para activar la caché por empresa
            request.state.tenant = SimpleNamespace(id=empresa_id)
            return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_dashboard_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_dashboard_200_forma():
    async with _cliente(_app(_FakeDashboard())) as c:
        r = await c.get("/api/v1/obras/dashboard")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kpis_mes"]["ingreso_total"] == "650000.00"       # Decimal como string
    assert body["kpis_mes"]["semaforo_utilidad"] == "verde"
    assert body["maquinas"]["ocupadas_hoy"][0]["operador_nombre"] == "Juan Pérez"
    assert body["maquinas"]["top_mes"][0]["ingreso"] == "600000.00"
    assert body["alertas"][0]["severidad"] == "rojo"
    assert body["alertas"][0]["ruta"] == "/maquinas"
    assert body["conteos"]["cotizaciones_por_vencer"] == 1


async def test_dashboard_requiere_admin():
    """Vista financiera: el vendedor no ve el cockpit (403)."""
    async with _cliente(_app(_FakeDashboard(), rol="vendedor")) as c:
        r = await c.get("/api/v1/obras/dashboard")
    assert r.status_code == 403, r.text


async def test_dashboard_gateado_por_obras():
    async with _cliente(_app(_FakeDashboard(), caps=frozenset())) as c:
        r = await c.get("/api/v1/obras/dashboard")
    assert r.status_code == 404, r.text


async def test_dashboard_cachea_segundo_hit():
    """Con tenant resuelto, el 2º request se sirve de la caché (no recomputa el compositor)."""
    dashboard_cache.clear()
    fake = _FakeDashboard()
    async with _cliente(_app(fake, empresa_id=42)) as c:
        r1 = await c.get("/api/v1/obras/dashboard")
        r2 = await c.get("/api/v1/obras/dashboard")
    assert r1.status_code == 200 and r2.status_code == 200
    assert fake.llamadas == 1            # el 2º hit no volvió a llamar a construir()
    assert r1.json() == r2.json()


async def test_dashboard_sin_tenant_no_cachea():
    """App mínima (sin tenant resuelto): siempre recalcula (no cachea entre requests)."""
    fake = _FakeDashboard()
    async with _cliente(_app(fake)) as c:
        await c.get("/api/v1/obras/dashboard")
        await c.get("/api/v1/obras/dashboard")
    assert fake.llamadas == 2


# =====================================================================================================
# (2) Integración real (Postgres efímero)
# =====================================================================================================
async def _seed_completo(s: AsyncSession) -> int:
    """Siembra un tenant con ingreso de alquiler, gastos, compras, viaje, cotización y mantenimiento
    vencido. Devuelve el maquina_id (para chequear el ref_id de la alerta)."""
    hoy = today_co()
    primero = hoy.replace(day=1)
    cid = (
        await s.execute(text("INSERT INTO clientes (nombre) VALUES ('Alcaldía') RETURNING id"))
    ).scalar_one()
    oid = (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre, estado) VALUES (:c,'Vía','EN_EJECUCION') RETURNING id"),
            {"c": cid},
        )
    ).scalar_one()
    tid = (
        await s.execute(
            text(
                "INSERT INTO trabajadores (tipo_vinculacion, documento, nombres, apellidos, cargo) "
                "VALUES ('DIRECTO','111','Juan','Pérez','Operador') RETURNING id"
            )
        )
    ).scalar_one()
    mid = (
        await s.execute(
            text(
                "INSERT INTO maquinas (codigo,nombre,tipo,precio_hora_default,estado) "
                "VALUES ('M-1','Vibro','vibro',150000,'OCUPADA') RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO asignaciones_maquina_obra "
            "(maquina_id,obra_id,fecha_inicio,precio_hora,minimo_horas,operador_id,activa) "
            "VALUES (:m,:o,:ini,100000,1,:t,true)"
        ),
        {"m": mid, "o": oid, "ini": primero, "t": tid},
    )
    await s.execute(
        text(
            "INSERT INTO registros_horas_maquina (maquina_id,obra_id,fecha,horas_trabajadas,horas_facturables) "
            "VALUES (:m,:o,:f,6,6)"
        ),
        {"m": mid, "o": oid, "f": hoy},
    )
    # Gastos: uno normal, uno pendiente de revisión.
    await s.execute(text("INSERT INTO gastos (categoria, monto) VALUES ('otros', 200000)"))
    await s.execute(
        text("INSERT INTO gastos (categoria, monto, requiere_revision) VALUES ('otros', 30000, true)")
    )
    # Compras: una de costo (no viaje) y un viaje de material (aporta su resbalo al ingreso).
    await s.execute(text("INSERT INTO compras (total, es_viaje_material) VALUES (150000, false)"))
    await s.execute(
        text("INSERT INTO compras (total, es_viaje_material, resbalo) VALUES (80000, true, 50000)")
    )
    # Cotización ENVIADA venciendo en 3 días (≤5 → por vencer).
    await s.execute(
        text(
            "INSERT INTO cotizaciones_obra (numero, cliente_id, nombre_obra, estado, vigencia_dias) "
            "VALUES ('PIM-001-2026', :c, 'Vía nueva', 'ENVIADA', 3)"
        ),
        {"c": cid},
    )
    # Mantenimiento con próximo servicio VENCIDO por fecha (ayer).
    await s.execute(
        text(
            "INSERT INTO mantenimientos (maquina_id, tipo, fecha, descripcion, costo, proximo_en_fecha) "
            "VALUES (:m, 'PREVENTIVO', :f, 'aceite', 100000, :ayer)"
        ),
        {"m": mid, "f": primero, "ayer": hoy - timedelta(days=1)},
    )
    await s.commit()
    return mid


async def test_dashboard_integracion_kpis_y_secciones(tenant):
    dashboard_cache.clear()
    async with AsyncSession(tenant.engine) as s:
        maquina_id = await _seed_completo(s)

    async with AsyncSession(tenant.engine) as s:
        dash = await DashboardConstruccionService(s, frozenset({"obras", "cotizaciones_aiu"})).construir()

    k = dash.kpis_mes
    assert k.ingreso_alquiler == Decimal("600000")     # 6 h × 100000 (precio pactado, LATERAL)
    assert k.resbalos == Decimal("50000")
    assert k.ingreso_total == Decimal("650000")
    assert k.gastos == Decimal("230000")               # 200000 + 30000
    assert k.compras == Decimal("150000")              # el viaje NO cuenta como gasto (solo su resbalo)
    assert k.gasto_total == Decimal("380000")
    assert k.utilidad_estimada == Decimal("270000")
    assert k.semaforo_utilidad == "verde"              # margen 41.5% ≥ 3%

    assert dash.maquinas.total == 1
    assert dash.maquinas.por_estado == {"OCUPADA": 1}
    ocupada = dash.maquinas.ocupadas_hoy[0]
    assert ocupada.maquina == "Vibro"
    assert ocupada.obra_nombre == "Vía"
    assert ocupada.operador_nombre == "Juan Pérez"
    assert ocupada.horas_hoy == Decimal("6")
    assert ocupada.ingreso_hoy == Decimal("600000")
    top = dash.maquinas.top_mes[0]
    assert top.horas == Decimal("6") and top.ingreso == Decimal("600000")

    assert dash.conteos.gastos_por_revisar == 1
    assert dash.conteos.cotizaciones_por_vencer == 1
    assert dash.conteos.colitas == 0                   # sin capacidad cartera_alquiler

    vencidas = [a for a in dash.alertas if a.tipo == "mantenimiento_vencido"]
    assert len(vencidas) == 1
    assert vencidas[0].severidad == "rojo"
    assert vencidas[0].ref_id == maquina_id


async def test_dashboard_cotizaciones_degradan_sin_capacidad(tenant):
    """Sin `cotizaciones_aiu`, el badge de cotizaciones por vencer queda en 0 (no se consulta)."""
    async with AsyncSession(tenant.engine) as s:
        await _seed_completo(s)
    async with AsyncSession(tenant.engine) as s:
        dash = await DashboardConstruccionService(s, frozenset({"obras"})).construir()
    assert dash.conteos.cotizaciones_por_vencer == 0


async def test_dashboard_vacio_no_revienta(tenant):
    """Un tenant sin datos devuelve el cockpit con ceros (no falla por listas/sumas vacías)."""
    async with AsyncSession(tenant.engine) as s:
        dash = await DashboardConstruccionService(s, frozenset({"obras"})).construir()
    assert dash.kpis_mes.ingreso_total == Decimal("0")
    assert dash.kpis_mes.margen_pct == Decimal("0")
    assert dash.maquinas.total == 0
    assert dash.alertas == []
    assert dash.conteos.gastos_por_revisar == 0
