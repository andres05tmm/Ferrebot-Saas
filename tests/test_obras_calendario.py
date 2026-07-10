"""Calendario de obra (GET /obras/calendario, /obras/calendario/dia — commit 2) — doble capa.

(0) AISLAMIENTO multi-tenant (invariante crítico): el calendario de la empresa A jamás ve datos de la B.
(1) Wiring HTTP con compositor FAKE (patrón `test_obras_dashboard.py`): gate `obras` (404), rol vendedor
    accede, y que `/obras/calendario` y `/obras/calendario/dia` NO caen en `/obras/{obra_id}`.
(2) Integración real contra Postgres efímero: seed multi-origen (horas con operador resuelto, reporte,
    asistencia con obra y administrativa, mantenimiento hecho + próximo, consumo, asignaciones que cruzan
    el borde del mes, hito por fecha_inicio), conteos/horas del mes, filtros, sin claves de dinero y
    degradación por capacidad `nomina`.
"""
from datetime import date
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from modules.obra.calendario import CalendarioObraService
from modules.obra.router import get_calendario_service, router
from modules.obra.schemas import (
    CalendarioMes,
    ConteosDiaCalendario,
    DetalleDiaCalendario,
    DiaCalendario,
)

# Los repos del compositor cruzan FKs entre módulos: registrarlos en la metadata del ORM al correr
# este archivo en aislamiento.
import modules.obra.models  # noqa: E402,F401
import modules.maquinaria.models  # noqa: E402,F401
import modules.trabajadores.models  # noqa: E402,F401
import modules.inventario.models  # noqa: E402,F401

_CAPS_TODO = frozenset({"obras", "maquinaria", "nomina"})
_ANIO, _MES = 2026, 3


# =====================================================================================================
# (0) AISLAMIENTO multi-tenant — invariante crítico
# =====================================================================================================
async def _seed_ingreso(engine) -> None:
    """Siembra máquina + obra + asignación activa (feb→abierta) + un parte de horas en 2026-03-12."""
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
                    "VALUES ('M-1','Retro','retro',150000,'OCUPADA') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO asignaciones_maquina_obra "
                "(maquina_id,obra_id,fecha_inicio,precio_hora,minimo_horas,activa) "
                "VALUES (:m,:o,'2026-02-15',100000,1,true)"
            ),
            {"m": mid, "o": oid},
        )
        await s.execute(
            text(
                "INSERT INTO registros_horas_maquina (maquina_id,obra_id,fecha,horas_trabajadas,horas_facturables) "
                "VALUES (:m,:o,'2026-03-12',8,8)"
            ),
            {"m": mid, "o": oid},
        )
        await s.commit()


async def test_calendario_A_no_ve_datos_de_B(tenant_factory):
    """El calendario de A ve su actividad; el de B (vacío) no ve nada de A — sin cruce entre empresas."""
    empresa_a = await tenant_factory()
    empresa_b = await tenant_factory()
    await _seed_ingreso(empresa_a.engine)

    async with AsyncSession(empresa_a.engine) as sa:
        mes_a = await CalendarioObraService(sa, _CAPS_TODO).mes(_ANIO, _MES)
        dia_a = await CalendarioObraService(sa, _CAPS_TODO).dia(date(2026, 3, 12))
    async with AsyncSession(empresa_b.engine) as sb:
        mes_b = await CalendarioObraService(sb, _CAPS_TODO).mes(_ANIO, _MES)
        dia_b = await CalendarioObraService(sb, _CAPS_TODO).dia(date(2026, 3, 12))

    assert any(d.conteos.horas_maquina == 1 for d in mes_a.dias)
    assert len(dia_a.horas_maquina) == 1
    assert mes_b.dias == []                 # B no ve nada de A
    assert dia_b.horas_maquina == []


# =====================================================================================================
# (1) Wiring HTTP con compositor FAKE
# =====================================================================================================
def _mes() -> CalendarioMes:
    return CalendarioMes(
        anio=2026, mes=7,
        dias=[DiaCalendario(
            fecha=date(2026, 7, 9), horas_maquina_total=Decimal("8"),
            conteos=ConteosDiaCalendario(horas_maquina=1),
        )],
    )


def _dia() -> DetalleDiaCalendario:
    return DetalleDiaCalendario(
        fecha=date(2026, 7, 9), horas_maquina=[], reportes=[], asistencia=[], mantenimientos=[],
        consumos=[], hitos=[], proximos_mantenimientos=[], planeado_maquinas=[], planeado_trabajadores=[],
    )


class _FakeCalendario:
    async def mes(self, anio, mes, **kw) -> CalendarioMes:
        return _mes()

    async def dia(self, fecha, **kw) -> DetalleDiaCalendario:
        return _dia()


def _app(*, rol="admin", caps=frozenset({"obras"})) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_calendario_service] = lambda: _FakeCalendario()
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    app.dependency_overrides[get_capacidades] = lambda: caps
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )


async def test_calendario_mes_200_no_cae_en_obra_id():
    """GET /obras/calendario responde el mes (no lo captura /obras/{obra_id})."""
    async with _cliente(_app(rol="vendedor")) as c:
        r = await c.get("/api/v1/obras/calendario?anio=2026&mes=7")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["anio"] == 2026 and body["mes"] == 7
    assert body["dias"][0]["horas_maquina_total"] == "8"      # Decimal como string
    assert body["dias"][0]["conteos"]["horas_maquina"] == 1


async def test_calendario_dia_200_no_cae_en_obra_id():
    """GET /obras/calendario/dia responde el detalle (no lo captura /obras/{obra_id})."""
    async with _cliente(_app(rol="vendedor")) as c:
        r = await c.get("/api/v1/obras/calendario/dia?fecha=2026-07-09")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fecha"] == "2026-07-09"
    assert "horas_maquina" in body and "planeado_trabajadores" in body


async def test_calendario_gateado_por_obras():
    async with _cliente(_app(caps=frozenset())) as c:
        r = await c.get("/api/v1/obras/calendario?anio=2026&mes=7")
        rd = await c.get("/api/v1/obras/calendario/dia?fecha=2026-07-09")
    assert r.status_code == 404, r.text
    assert rd.status_code == 404, rd.text


async def test_calendario_vendedor_accede():
    """Vista de operación (sin dinero): el vendedor SÍ entra (a diferencia del cockpit financiero)."""
    async with _cliente(_app(rol="vendedor")) as c:
        r = await c.get("/api/v1/obras/calendario?anio=2026&mes=7")
    assert r.status_code == 200, r.text


# =====================================================================================================
# (2) Integración real (Postgres efímero)
# =====================================================================================================
async def _seed_completo(s: AsyncSession) -> dict:
    """Siembra un mes (2026-03) con actividad de cada origen. Devuelve ids para las aserciones."""
    cid = (
        await s.execute(text("INSERT INTO clientes (nombre) VALUES ('Alcaldía') RETURNING id"))
    ).scalar_one()
    oid = (
        await s.execute(
            text(
                "INSERT INTO obras (cliente_id, nombre, estado, fecha_inicio) "
                "VALUES (:c,'Vía Llanogrande','EN_EJECUCION','2026-03-10') RETURNING id"
            ),
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
                "VALUES ('M-1','Retroexcavadora CAT 416','retro',150000,'OCUPADA') RETURNING id"
            )
        )
    ).scalar_one()
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES ('Cemento','bulto',30000,19,false,true) RETURNING id"
            )
        )
    ).scalar_one()
    # Asignación máquina→obra que CRUZA el borde del mes (feb→abierta): cubre todo marzo. Operador Juan.
    await s.execute(
        text(
            "INSERT INTO asignaciones_maquina_obra "
            "(maquina_id,obra_id,fecha_inicio,fecha_fin,precio_hora,minimo_horas,operador_id,activa) "
            "VALUES (:m,:o,'2026-02-15',NULL,100000,1,:t,true)"
        ),
        {"m": mid, "o": oid, "t": tid},
    )
    # Parte de horas SIN operador en el parte → el nombre lo resuelve la asignación vigente (COALESCE).
    await s.execute(
        text(
            "INSERT INTO registros_horas_maquina (maquina_id,obra_id,fecha,horas_trabajadas,horas_facturables) "
            "VALUES (:m,:o,'2026-03-12',8,8)"
        ),
        {"m": mid, "o": oid},
    )
    # Mantenimiento HECHO 03-05 con PRÓXIMO 03-20 (ambos dentro del mes).
    await s.execute(
        text(
            "INSERT INTO mantenimientos (maquina_id, tipo, fecha, descripcion, costo, proximo_en_fecha) "
            "VALUES (:m,'PREVENTIVO','2026-03-05','cambio de aceite',100000,'2026-03-20')"
        ),
        {"m": mid},
    )
    # Asistencia: con obra (03-12) y ADMINISTRATIVA obra NULL (03-13).
    await s.execute(
        text(
            "INSERT INTO registros_asistencia (trabajador_id, fecha, obra_id, horas_trabajadas) "
            "VALUES (:t,'2026-03-12',:o,8)"
        ),
        {"t": tid, "o": oid},
    )
    await s.execute(
        text(
            "INSERT INTO registros_asistencia (trabajador_id, fecha, obra_id, horas_trabajadas) "
            "VALUES (:t,'2026-03-13',NULL,8)"
        ),
        {"t": tid},
    )
    await s.execute(
        text(
            "INSERT INTO consumos_inventario (obra_id, producto_id, fecha, cantidad, costo_unitario) "
            "VALUES (:o,:p,'2026-03-12',10,500)"
        ),
        {"o": oid, "p": pid},
    )
    await s.execute(
        text(
            "INSERT INTO reportes_diarios_obra (obra_id, fecha, avance_descripcion, m2_ejecutados) "
            "VALUES (:o,'2026-03-12','fundida de placa',120)"
        ),
        {"o": oid},
    )
    # Asignación trabajador→obra (marzo→abierta): cubre todo marzo.
    await s.execute(
        text(
            "INSERT INTO asignaciones_trabajador_obra (trabajador_id, obra_id, fecha_inicio, fecha_fin, activa) "
            "VALUES (:t,:o,'2026-03-01',NULL,true)"
        ),
        {"t": tid, "o": oid},
    )
    await s.commit()
    return {"obra": oid, "maquina": mid, "trabajador": tid, "producto": pid}


async def test_calendario_mes_conteos(tenant):
    async with AsyncSession(tenant.engine) as s:
        await _seed_completo(s)
    async with AsyncSession(tenant.engine) as s:
        mes = await CalendarioObraService(s, _CAPS_TODO).mes(_ANIO, _MES)

    por_fecha = {d.fecha.isoformat(): d for d in mes.dias}
    # Ambas asignaciones (máquina feb→abierta, trabajador marzo→abierta) cubren todo marzo → 31 días.
    assert len(mes.dias) == 31
    d12 = por_fecha["2026-03-12"].conteos
    assert d12.horas_maquina == 1 and d12.reportes == 1 and d12.asistencias == 1 and d12.consumos == 1
    assert d12.maquinas_asignadas == 1 and d12.trabajadores_asignados == 1
    assert por_fecha["2026-03-12"].horas_maquina_total == Decimal("8")
    assert por_fecha["2026-03-05"].conteos.mantenimientos == 1
    assert por_fecha["2026-03-20"].conteos.proximos_mantenimientos == 1
    assert por_fecha["2026-03-10"].conteos.hitos == 1          # fecha_inicio de la obra
    assert por_fecha["2026-03-13"].conteos.asistencias == 1    # día administrativo


async def test_calendario_mes_dias_sin_actividad_no_vienen(tenant):
    """Un mes anterior a cualquier asignación/actividad no trae días (2025-01 < feb 2026)."""
    async with AsyncSession(tenant.engine) as s:
        await _seed_completo(s)
    async with AsyncSession(tenant.engine) as s:
        mes = await CalendarioObraService(s, _CAPS_TODO).mes(2025, 1)
    assert mes.dias == []


async def test_calendario_dia_detalle_y_nombres(tenant):
    async with AsyncSession(tenant.engine) as s:
        ids = await _seed_completo(s)
    async with AsyncSession(tenant.engine) as s:
        dia = await CalendarioObraService(s, _CAPS_TODO).dia(date(2026, 3, 12))

    h = dia.horas_maquina[0]
    assert h.maquina == "Retroexcavadora CAT 416" and h.obra == "Vía Llanogrande"
    assert h.operador == "Juan Pérez" and h.operador_id == ids["trabajador"]   # COALESCE parte→asignación
    assert h.horas_trabajadas == Decimal("8")
    assert dia.reportes[0].m2_ejecutados == Decimal("120")
    assert dia.consumos[0].producto == "Cemento" and dia.consumos[0].cantidad == Decimal("10")
    assert dia.asistencia[0].obra == "Vía Llanogrande"           # asistencia con obra
    pm = dia.planeado_maquinas[0]
    assert pm.operador == "Juan Pérez" and pm.fecha_fin is None
    assert dia.planeado_trabajadores[0].trabajador == "Juan Pérez"


async def test_calendario_dia_asistencia_administrativa(tenant):
    """El 03-13 la asistencia es administrativa: obra NULL (la UI pinta 'Administrativo')."""
    async with AsyncSession(tenant.engine) as s:
        await _seed_completo(s)
    async with AsyncSession(tenant.engine) as s:
        dia = await CalendarioObraService(s, _CAPS_TODO).dia(date(2026, 3, 13))
    assert len(dia.asistencia) == 1
    assert dia.asistencia[0].obra is None


async def test_calendario_filtros_acotan(tenant):
    async with AsyncSession(tenant.engine) as s:
        ids = await _seed_completo(s)
    async with AsyncSession(tenant.engine) as s:
        svc = CalendarioObraService(s, _CAPS_TODO)
        d_obra_ok = await svc.dia(date(2026, 3, 12), obra_id=ids["obra"])
        d_obra_no = await svc.dia(date(2026, 3, 12), obra_id=ids["obra"] + 999)
        d_maq_no = await svc.dia(date(2026, 3, 12), maquina_id=ids["maquina"] + 999)
        d_trab_no = await svc.dia(date(2026, 3, 13), trabajador_id=ids["trabajador"] + 999)

    assert len(d_obra_ok.horas_maquina) == 1 and len(d_obra_ok.consumos) == 1
    assert d_obra_no.horas_maquina == [] and d_obra_no.reportes == [] and d_obra_no.consumos == []
    assert d_obra_no.planeado_maquinas == [] and d_obra_no.planeado_trabajadores == []
    assert d_maq_no.horas_maquina == []
    assert d_trab_no.asistencia == []


async def test_calendario_dia_sin_claves_de_dinero(tenant):
    """El detalle serializado NO contiene ninguna clave de dinero (precio/costo)."""
    async with AsyncSession(tenant.engine) as s:
        await _seed_completo(s)
    async with AsyncSession(tenant.engine) as s:
        dia = await CalendarioObraService(s, _CAPS_TODO).dia(date(2026, 3, 12))

    def _claves(obj) -> set:
        out: set = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                out.add(k)
                out |= _claves(v)
        elif isinstance(obj, list):
            for it in obj:
                out |= _claves(it)
        return out

    claves = _claves(dia.model_dump())
    assert not any("precio" in k or "costo" in k for k in claves), claves


async def test_calendario_degrada_sin_nomina(tenant):
    """Sin capacidad `nomina`: asistencia y planeado_trabajadores vacíos (mes y detalle)."""
    async with AsyncSession(tenant.engine) as s:
        await _seed_completo(s)
    caps = frozenset({"obras", "maquinaria"})
    async with AsyncSession(tenant.engine) as s:
        svc = CalendarioObraService(s, caps)
        dia = await svc.dia(date(2026, 3, 13))
        mes = await svc.mes(_ANIO, _MES)

    assert dia.asistencia == [] and dia.planeado_trabajadores == []
    por_fecha = {d.fecha.isoformat(): d for d in mes.dias}
    assert por_fecha["2026-03-12"].conteos.asistencias == 0
    assert por_fecha["2026-03-12"].conteos.trabajadores_asignados == 0
    assert por_fecha["2026-03-12"].conteos.horas_maquina == 1   # maquinaria sigue activa
