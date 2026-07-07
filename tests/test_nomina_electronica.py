"""Nómina electrónica (Fase 7 PIM): pipeline de transmisión del CUNE a DIAN vía MATIAS.

MATIAS SIEMPRE MOCKEADO (regla de oro fiscal: jamás golpear MATIAS/DIAN real; la transmisión real es
GO-LIVE GATED). Cubre:
  - parser + orquestación del método nuevo `MatiasClient.transmitir_nomina` (httpx.MockTransport, cero red);
  - pipeline sobre Postgres efímero: cada DIRECTO recibe cune_dian + estado TRANSMITIDO; el PATACALIENTE
    NO (queda PENDIENTE, excluido — spec 08);
  - INVARIANTE idempotencia (test-primero): re-transmitir NO produce un segundo CUNE ni una segunda llamada
    efectiva (skip por TRANSMITIDO); RECHAZADO es terminal (no se reprocesa); ERROR (5xx) sí es reintentable;
  - gate del periodo (ABIERTO → 409/bloqueado);
  - aislamiento multi-tenant (transmitir en A no toca los detalles de B);
  - gate del endpoint (flag `nomina_electronica` + rol admin) y traducción del job ARQ.
"""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from arq import Retry
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal
from core.auth.deps import get_current_user
from core.auth.features import get_capacidades
from modules.facturacion.matias_client import (
    CUNE_MIN_LEN,
    MatiasClient,
    MatiasCredenciales,
    TransmisionNominaResultado,
    _parsear_transmision_nomina,
)
from modules.nomina.electronica import (
    NominaElectronicaService,
    ResumenTransmision,
    construir_payload_nomina,
    transmitir_nomina as job_transmitir_nomina,
)
from modules.nomina.errors import PeriodoBloqueado, PeriodoNominaInexistente
from modules.nomina.repository import SqlNominaRepository
from modules.nomina.router import (
    get_enqueuer,
    get_nomina_service,
    get_tenant_id,
    router as nomina_router,
)
from modules.nomina.schemas import AsistenciaCrear, PeriodoCrear
from modules.nomina.service import NominaService

_INICIO = date(2026, 7, 1)
_FIN = date(2026, 7, 15)
_CUNE = "c" * 96   # CUNE DIAN realista (~SHA-384 hex); >= CUNE_MIN_LEN
_CRED = MatiasCredenciales(email="bot@pim.co", password="x", base_url="https://matias.test/api")


class _Cfg:
    """Config fiscal mínima que consume `NominaElectronicaService` (solo el ambiente)."""

    ambiente = "pruebas"


# =============================================================================
# 1) parser puro + orquestación httpx (cero red)
# =============================================================================

def test_parsear_transmision_exito():
    res = _parsear_transmision_nomina({"success": True, "cune": _CUNE})
    assert res.ok is True and res.categoria == "aceptada" and res.cune == _CUNE


def test_parsear_transmision_cune_corto_es_error():
    # success pero sin CUNE largo → 'error' (reintentable), no una transmisión válida.
    res = _parsear_transmision_nomina({"success": True, "cune": "abc"})
    assert res.ok is False and res.categoria == "error"
    assert len("abc") < CUNE_MIN_LEN


def test_parsear_transmision_rechazo():
    res = _parsear_transmision_nomina(
        {"success": False, "message": "Rechazado", "errors": {"worker.dni": "requerido"}}
    )
    assert res.ok is False and res.categoria == "rechazada"
    assert "Rechazado" in res.error_msg and "worker.dni: requerido" in res.error_msg


class _HandlerNomina:
    """MockTransport: login + /payroll canned; traza de paths (sin red real)."""

    def __init__(self, *, payroll=None, status_payroll=200):
        self._payroll = payroll if payroll is not None else {}
        self._status = status_payroll
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(request.url.path)
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"token": "T", "expires_in": 3600})
        if request.url.path.endswith("/payroll"):
            return httpx.Response(self._status, json=self._payroll)
        return httpx.Response(404, json={})


def _client(handler: _HandlerNomina) -> MatiasClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=_CRED.base_url)
    return MatiasClient(_CRED, client=http)


async def test_transmitir_nomina_aceptada_mock():
    handler = _HandlerNomina(payroll={"success": True, "cune": _CUNE})
    res = await _client(handler).transmitir_nomina({"trabajador": {}})
    assert res.ok is True and res.categoria == "aceptada" and res.cune == _CUNE
    assert any(p.endswith("/payroll") for p in handler.paths)


async def test_transmitir_nomina_5xx_es_error_transitorio():
    # 5xx clasificado ANTES de parsear como error (reintentable), nunca rechazo de negocio.
    handler = _HandlerNomina(payroll={"message": "boom"}, status_payroll=503)
    res = await _client(handler).transmitir_nomina({"trabajador": {}})
    assert res.ok is False and res.categoria == "error"


async def test_transmitir_nomina_rechazo_mock():
    handler = _HandlerNomina(payroll={"success": False, "message": "NIT inválido"})
    res = await _client(handler).transmitir_nomina({"trabajador": {}})
    assert res.ok is False and res.categoria == "rechazada"


# =============================================================================
# 2) doble de MATIAS + helpers de seed para el pipeline
# =============================================================================

class _FakeMatias:
    """MatiasClient fake: `transmitir_nomina` devuelve un resultado canned y cuenta llamadas/payloads."""

    def __init__(self, resultado: TransmisionNominaResultado):
        self._resultado = resultado
        self.llamadas = 0
        self.payloads: list[dict] = []

    async def transmitir_nomina(self, payload: dict) -> TransmisionNominaResultado:
        self.llamadas += 1
        self.payloads.append(payload)
        return self._resultado


def _aceptada() -> TransmisionNominaResultado:
    return TransmisionNominaResultado(True, cune=_CUNE, categoria="aceptada",
                                      raw={"success": True, "cune": _CUNE})


async def _seed_parametros(s: AsyncSession) -> None:
    await s.execute(
        text(
            "INSERT INTO parametros_legales "
            "(vigente_desde, smmlv, auxilio_transporte, salud_empleado_pct, pension_empleado_pct, "
            " salud_empleador_pct, pension_empleador_pct, arl_pct) "
            "VALUES ('2026-01-01', 1750905, 249095, 0.04, 0.04, 0.085, 0.12, 0.0522)"
        )
    )


async def _seed_trabajador(s: AsyncSession, *, tipo, documento, salario=None, tarifa=None) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO trabajadores "
                "(tipo_vinculacion, documento, nombres, apellidos, cargo, salario_base, tarifa_hora) "
                "VALUES (:tipo, :doc, 'Ana', 'Ruiz', 'Operador', :sal, :tar) RETURNING id"
            ),
            {"tipo": tipo, "doc": documento, "sal": salario, "tar": tarifa},
        )
    ).scalar_one()


async def _preparar_periodo_liquidado(tenant) -> tuple[int, int, int, int]:
    """Siembra 2 DIRECTO + 1 PATACALIENTE, crea + liquida + CIERRA un periodo. (pid, d1, d2, pc)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        d1 = await _seed_trabajador(s, tipo="DIRECTO", documento="d1", salario="1500000")
        d2 = await _seed_trabajador(s, tipo="DIRECTO", documento="d2", salario="2000000")
        pc = await _seed_trabajador(s, tipo="PATACALIENTE", documento="p1", tarifa="12000")
        svc = NominaService(SqlNominaRepository(s))
        periodo = await svc.crear_periodo(
            PeriodoCrear(tipo="QUINCENAL", fecha_inicio=_INICIO, fecha_fin=_FIN, nombre="Q1")
        )
        await s.commit()
        pid = periodo.id
        for tid in (d1, d2, pc):
            for i in range(6):   # 6 días × 8 h de actividad (para que los tres se liquiden)
                await svc.registrar_asistencia(
                    AsistenciaCrear(trabajador_id=tid, fecha=_INICIO + timedelta(days=i),
                                    horas_trabajadas=Decimal("8"))
                )
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = NominaService(SqlNominaRepository(s))
        await svc.liquidar_periodo(pid)
        await svc.cerrar_periodo(pid)   # LIQUIDADO: requisito para transmitir
        await s.commit()
    return pid, d1, d2, pc


async def _transmitir(tenant, pid, matias, cfg=None) -> ResumenTransmision:
    cfg = cfg or _Cfg()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = NominaElectronicaService(SqlNominaRepository(s), matias, cfg)
        resumen = await svc.transmitir_periodo(pid)
        await s.commit()
    return resumen


async def _detalle(tenant, pid, tid):
    """(estado_transmision, cune_dian, intentos, fecha) del detalle."""
    async with AsyncSession(tenant.engine) as s:
        return (
            await s.execute(
                text(
                    "SELECT estado_transmision, cune_dian, intentos_transmision, fecha_transmision_dian "
                    "FROM detalles_liquidacion WHERE periodo_id=:p AND trabajador_id=:t"
                ),
                {"p": pid, "t": tid},
            )
        ).first()


# =============================================================================
# 3) pipeline: transmisión de DIRECTOS, patacaliente excluido
# =============================================================================

async def test_transmite_directos_no_patacaliente(tenant):
    pid, d1, d2, pc = await _preparar_periodo_liquidado(tenant)
    matias = _FakeMatias(_aceptada())

    resumen = await _transmitir(tenant, pid, matias)

    assert resumen.transmitidos == 2 and resumen.rechazados == 0 and resumen.errores == 0
    assert matias.llamadas == 2   # SOLO los dos DIRECTO; el patacaliente no genera CUNE (spec 08)
    for tid in (d1, d2):
        estado, cune, intentos, fecha = await _detalle(tenant, pid, tid)
        assert estado == "TRANSMITIDO" and cune == _CUNE and intentos == 1 and fecha is not None
    # patacaliente intacto: PENDIENTE, sin CUNE
    estado_pc, cune_pc, intentos_pc, _ = await _detalle(tenant, pid, pc)
    assert estado_pc == "PENDIENTE" and cune_pc is None and intentos_pc == 0
    # el payload marca el trabajador como DIRECTO (interfaz [VERIFICAR])
    assert all(p["trabajador"]["tipo_contrato"] == "DIRECTO" for p in matias.payloads)


async def test_reintentar_no_duplica_cune(tenant):
    """IDEMPOTENCIA: re-transmitir un periodo ya TRANSMITIDO no llama a MATIAS ni cambia el CUNE."""
    pid, d1, d2, _ = await _preparar_periodo_liquidado(tenant)
    matias = _FakeMatias(_aceptada())

    r1 = await _transmitir(tenant, pid, matias)
    assert r1.transmitidos == 2 and matias.llamadas == 2
    cune_1 = (await _detalle(tenant, pid, d1))[1]

    r2 = await _transmitir(tenant, pid, matias)   # replay
    assert r2.transmitidos == 0 and r2.reintentar is False
    assert matias.llamadas == 2                    # NO hubo segunda llamada efectiva
    estado, cune_2, intentos, _ = await _detalle(tenant, pid, d1)
    assert estado == "TRANSMITIDO" and cune_2 == cune_1 and intentos == 1   # mismo CUNE, no re-intenta


async def test_rechazo_es_terminal(tenant):
    """RECHAZADO no se reprocesa: re-transmitir no vuelve a llamar a MATIAS (excluido del barrido)."""
    pid, d1, d2, _ = await _preparar_periodo_liquidado(tenant)
    rechazo = TransmisionNominaResultado(False, categoria="rechazada", error_msg="NIT inválido",
                                         raw={"success": False, "message": "NIT inválido"})
    matias = _FakeMatias(rechazo)

    r1 = await _transmitir(tenant, pid, matias)
    assert r1.rechazados == 2 and r1.transmitidos == 0 and r1.reintentar is False
    estado, cune, intentos, _ = await _detalle(tenant, pid, d1)
    assert estado == "RECHAZADO" and cune is None and intentos == 1

    r2 = await _transmitir(tenant, pid, matias)   # terminal: nada por reprocesar
    assert r2.rechazados == 0 and matias.llamadas == 2   # no re-llama
    assert (await _detalle(tenant, pid, d1))[0] == "RECHAZADO"


async def test_5xx_es_error_reintentable(tenant):
    """ERROR (5xx transitorio) sí es reintentable: re-transmitir vuelve a procesar PENDIENTE/ERROR."""
    pid, d1, d2, _ = await _preparar_periodo_liquidado(tenant)
    err = TransmisionNominaResultado(False, categoria="error", error_msg="MATIAS respondió HTTP 503")
    matias = _FakeMatias(err)

    r1 = await _transmitir(tenant, pid, matias)
    assert r1.errores == 2 and r1.reintentar is True and r1.transmitidos == 0
    estado, cune, intentos, _ = await _detalle(tenant, pid, d1)
    assert estado == "ERROR" and cune is None and intentos == 1

    # el mismo error se vuelve a intentar y suma otro intento (idempotente sobre el estado ERROR)
    r2 = await _transmitir(tenant, pid, matias)
    assert r2.errores == 2 and matias.llamadas == 4
    assert (await _detalle(tenant, pid, d1))[2] == 2   # intentos incrementó


async def test_recupera_de_error_a_transmitido(tenant):
    """Tras un ERROR, un reintento exitoso deja el detalle TRANSMITIDO con su CUNE (auto-cura)."""
    pid, d1, _, _ = await _preparar_periodo_liquidado(tenant)
    err = TransmisionNominaResultado(False, categoria="error", error_msg="boom")
    await _transmitir(tenant, pid, _FakeMatias(err))
    assert (await _detalle(tenant, pid, d1))[0] == "ERROR"

    await _transmitir(tenant, pid, _FakeMatias(_aceptada()))
    estado, cune, intentos, fecha = await _detalle(tenant, pid, d1)
    assert estado == "TRANSMITIDO" and cune == _CUNE and intentos == 2 and fecha is not None


async def test_periodo_abierto_bloquea(tenant):
    """No se transmite un periodo ABIERTO: hay que cerrarlo antes (spec 08)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        svc = NominaService(SqlNominaRepository(s))
        periodo = await svc.crear_periodo(
            PeriodoCrear(tipo="QUINCENAL", fecha_inicio=_INICIO, fecha_fin=_FIN)
        )
        await s.commit()
        pid = periodo.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = NominaElectronicaService(SqlNominaRepository(s), _FakeMatias(_aceptada()), _Cfg())
        with pytest.raises(PeriodoBloqueado):
            await svc.transmitir_periodo(pid)


async def test_periodo_inexistente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = NominaElectronicaService(SqlNominaRepository(s), _FakeMatias(_aceptada()), _Cfg())
        with pytest.raises(PeriodoNominaInexistente):
            await svc.transmitir_periodo(999_999)


async def test_aislamiento_transmision_no_cruza_tenants(tenant_factory):
    """Transmitir en la empresa A no toca los detalles de la empresa B (DB-per-tenant)."""
    a = await tenant_factory()
    b = await tenant_factory()
    pid_a, da1, da2, _ = await _preparar_periodo_liquidado(a)
    pid_b, db1, db2, _ = await _preparar_periodo_liquidado(b)

    await _transmitir(a, pid_a, _FakeMatias(_aceptada()))

    assert (await _detalle(a, pid_a, da1))[0] == "TRANSMITIDO"
    # B quedó intacto: sus DIRECTO siguen PENDIENTE sin CUNE
    estado_b, cune_b, _, _ = await _detalle(b, pid_b, db1)
    assert estado_b == "PENDIENTE" and cune_b is None


# =============================================================================
# 4) payload puro
# =============================================================================

def test_construir_payload_ambiente_pruebas():
    periodo = SimpleNamespace(tipo="QUINCENAL", fecha_inicio=_INICIO, fecha_fin=_FIN)
    detalle = SimpleNamespace(
        dias_liquidados=Decimal("15"), salario_devengado=Decimal("750000"),
        auxilio_transporte=Decimal("124547.50"), valor_horas_extra=Decimal("0"),
        total_devengado=Decimal("874547.50"), salud_empleado=Decimal("30000"),
        pension_empleado=Decimal("30000"), total_deducciones=Decimal("60000"),
        neto_pagar=Decimal("814547.50"),
    )
    trab = SimpleNamespace(tipo_documento="CC", documento="123", apellidos="Ruiz",
                           nombres="Ana", salario_base=Decimal("1500000"))
    payload = construir_payload_nomina(periodo, detalle, trab, _Cfg())
    assert payload["ambiente"] == 2                       # pruebas → 2 (nunca producción sin go-live)
    assert payload["trabajador"]["numero_documento"] == "123"
    assert payload["devengados"]["total"] == "874547.50"
    assert payload["neto_pagar"] == "814547.50"


# =============================================================================
# 5) job ARQ: traducción de ResumenTransmision → semántica del worker
# =============================================================================

class _FakeServicio:
    def __init__(self, resumen: ResumenTransmision):
        self._resumen = resumen

    async def transmitir_nomina(self, periodo_id: int) -> ResumenTransmision:
        return self._resumen


def _ctx(resumen: ResumenTransmision, *, job_try: int = 1) -> dict:
    async def crear_servicio(_tid: int) -> _FakeServicio:
        return _FakeServicio(resumen)

    return {"crear_servicio": crear_servicio, "job_try": job_try}


async def test_job_reintenta():
    ctx = _ctx(ResumenTransmision(periodo_id=1, errores=1, reintentar=True))
    with pytest.raises(Retry):
        await job_transmitir_nomina(ctx, 7, 1)


async def test_job_dead_letter():
    ctx = _ctx(ResumenTransmision(periodo_id=1, errores=1, dead_letter=True))
    assert await job_transmitir_nomina(ctx, 7, 1) == "dead_letter"


async def test_job_transmitido():
    ctx = _ctx(ResumenTransmision(periodo_id=1, transmitidos=2))
    assert await job_transmitir_nomina(ctx, 7, 1) == "transmitido"


# =============================================================================
# 6) endpoint: gate de feature (nomina_electronica) + rol admin + encolado
# =============================================================================

class _FakeSvcHttp:
    def __init__(self, *, estado="LIQUIDADO", n=2, existe=True):
        self._estado, self._n, self._existe = estado, n, existe

    async def obtener_periodo(self, periodo_id):
        if not self._existe:
            raise PeriodoNominaInexistente(periodo_id)
        return SimpleNamespace(estado=self._estado)

    async def contar_directos_transmitibles(self, periodo_id):
        return self._n


class _FakeEnq:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def enqueue(self, job, *args):
        self.jobs.append((job, *args))


def _app(caps, svc, enq, *, rol="admin") -> FastAPI:
    app = FastAPI()
    app.include_router(nomina_router, prefix="/api/v1")

    async def _caps():
        return caps

    async def _svc():
        return svc

    async def _enqdep():
        return enq

    app.dependency_overrides[get_capacidades] = _caps
    app.dependency_overrides[get_nomina_service] = _svc
    app.dependency_overrides[get_enqueuer] = _enqdep
    app.dependency_overrides[get_tenant_id] = lambda: 7
    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pim", rol=rol)
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


async def test_endpoint_sin_flag_404():
    # tiene `nomina` (router) pero NO `nomina_electronica` (endpoint) → 404, sin encolar.
    enq = _FakeEnq()
    app = _app(frozenset({"nomina"}), _FakeSvcHttp(), enq)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/nomina/periodos/55/transmitir-dian")
    assert r.status_code == 404 and enq.jobs == []


async def test_endpoint_con_flag_encola_202():
    enq = _FakeEnq()
    app = _app(frozenset({"nomina", "nomina_electronica"}), _FakeSvcHttp(n=2), enq)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/nomina/periodos/55/transmitir-dian")
    assert r.status_code == 202
    body = r.json()
    assert body == {"periodo_id": 55, "estado": "LIQUIDADO", "transmisibles": 2, "encolado": True}
    assert enq.jobs == [("transmitir_nomina", 7, 55)]


async def test_endpoint_rol_vendedor_403():
    enq = _FakeEnq()
    app = _app(frozenset({"nomina", "nomina_electronica"}), _FakeSvcHttp(), enq, rol="vendedor")
    async with _cliente(app) as c:
        r = await c.post("/api/v1/nomina/periodos/55/transmitir-dian")
    assert r.status_code == 403 and enq.jobs == []


async def test_endpoint_periodo_abierto_409():
    enq = _FakeEnq()
    app = _app(frozenset({"nomina", "nomina_electronica"}), _FakeSvcHttp(estado="ABIERTO"), enq)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/nomina/periodos/55/transmitir-dian")
    assert r.status_code == 409 and enq.jobs == []


async def test_endpoint_periodo_inexistente_404():
    enq = _FakeEnq()
    app = _app(frozenset({"nomina", "nomina_electronica"}), _FakeSvcHttp(existe=False), enq)
    async with _cliente(app) as c:
        r = await c.post("/api/v1/nomina/periodos/55/transmitir-dian")
    assert r.status_code == 404 and enq.jobs == []
