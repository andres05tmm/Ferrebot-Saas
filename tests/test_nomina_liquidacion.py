"""Integración de nómina (Fase 4 PIM) contra Postgres efímero.

Cubre el flujo completo con persistencia real:
  - crear periodo CONGELA el snapshot de `parametros_legales` (aunque luego cambie la parametrización);
  - liquidación DIRECTO (caso sintético con params default provisionales [DEFINIR contador]) y
    PATACALIENTE (48 h × 12.000 = 576.000, spec 08);
  - INVARIANTE conciliación: Σ `costo_imputado` del prorrateo ≡ costo total liquidado del trabajador;
  - INVARIANTE idempotencia: re-liquidar / cerrar / pagar no duplican filas ni cambian el resultado;
  - aislamiento multi-tenant: la empresa B no ve periodos de la A.

Los valores de porcentajes/recargos son PROVISIONALES (mecánica fija; el contador confirma los reales):
por eso el caso DIRECTO ancla algunos valores concretos Y además cruza el detalle persistido contra el
motor puro con el MISMO snapshot (fidelidad de persistencia), en vez de hardcodear todos los centavos.
"""
import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.nomina.errors import ParametrosLegalesInexistentes, PeriodoBloqueado
from modules.nomina.repository import SqlNominaRepository
from modules.nomina.schemas import AsistenciaCrear, PeriodoCrear
from modules.nomina.service import (
    NominaService,
    _Asistencia,
    _snapshot_a_parametros,
    _TrabDirecto,
)
from services.calculations.nomina import liquidar_directo

_INICIO = date(2026, 7, 1)
_FIN = date(2026, 7, 15)


def _svc(s: AsyncSession) -> NominaService:
    return NominaService(SqlNominaRepository(s))


async def _seed_parametros(s: AsyncSession, *, smmlv="1750905", aux="249095") -> int:
    """Fila de parametros_legales 2026 (valores confirmados + %s provisionales por default de la tabla)."""
    return (
        await s.execute(
            text(
                "INSERT INTO parametros_legales "
                "(vigente_desde, smmlv, auxilio_transporte, salud_empleado_pct, pension_empleado_pct, "
                " salud_empleador_pct, pension_empleador_pct, arl_pct) "
                "VALUES ('2026-01-01', :smmlv, :aux, 0.04, 0.04, 0.085, 0.12, 0.0522) RETURNING id"
            ),
            {"smmlv": smmlv, "aux": aux},
        )
    ).scalar_one()


async def _seed_trabajador(
    s: AsyncSession, *, tipo="DIRECTO", documento="1", salario=None, tarifa=None
) -> int:
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


async def _seed_cliente(s: AsyncSession, nombre="Alcaldía") -> int:
    return (
        await s.execute(
            text("INSERT INTO clientes (nombre, saldo_fiado) VALUES (:n, 0) RETURNING id"),
            {"n": nombre},
        )
    ).scalar_one()


async def _seed_obra(s: AsyncSession, cliente_id: int, nombre="Vía") -> int:
    return (
        await s.execute(
            text("INSERT INTO obras (cliente_id, nombre) VALUES (:c, :n) RETURNING id"),
            {"c": cliente_id, "n": nombre},
        )
    ).scalar_one()


async def _asistencia(
    svc: NominaService, trabajador_id: int, dias: int, *, obra_id=None, desde=_INICIO, horas=8
):
    """Crea `dias` registros de asistencia consecutivos desde `desde` en una obra (o admin)."""
    for i in range(dias):
        await svc.registrar_asistencia(
            AsistenciaCrear(
                trabajador_id=trabajador_id, fecha=desde + timedelta(days=i),
                obra_id=obra_id, horas_trabajadas=Decimal(horas),
            )
        )


def _periodo_quincena() -> PeriodoCrear:
    return PeriodoCrear(tipo="QUINCENAL", fecha_inicio=_INICIO, fecha_fin=_FIN, nombre="Q1 jul 2026")


# --- crear periodo + snapshot ------------------------------------------------
async def test_crear_periodo_sin_parametros_falla(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(ParametrosLegalesInexistentes):
            await _svc(s).crear_periodo(_periodo_quincena())


async def test_crear_periodo_congela_snapshot(tenant):
    """El periodo congela los parámetros al crearse: cambiarlos DESPUÉS no altera el snapshot."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        assert periodo.estado == "ABIERTO"
        assert periodo.param_smmlv == Decimal("1750905")
        assert periodo.param_recargo_he_diurna == Decimal("1.25")   # default provisional de 0047
        assert periodo.param_horas_mes == Decimal("240")

    # muta la parametrización vigente
    async with AsyncSession(tenant.engine) as s:
        await s.execute(text("UPDATE parametros_legales SET smmlv = 9999999"))
        await s.commit()

    # el snapshot del periodo no cambió (freeze)
    async with AsyncSession(tenant.engine) as s:
        snap = (
            await s.execute(
                text("SELECT param_smmlv FROM periodos_nomina WHERE id=:id"), {"id": pid}
            )
        ).scalar_one()
        assert snap == Decimal("1750905")


# --- liquidación DIRECTO (acceptance) ----------------------------------------
async def test_liquidar_directo_acceptance(tenant):
    """15 días trabajados, salario 1.500.000, sin HE: valores concretos + cruce contra el motor puro."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        tid = await _seed_trabajador(s, documento="d1", salario="1500000")
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        await _asistencia(_svc(s), tid, 15)   # 15 días admin, sin HE
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        resumen = await _svc(s).liquidar_periodo(pid)
        await s.commit()
        assert resumen.trabajadores_liquidados == 1

    async with AsyncSession(tenant.engine) as s:
        svc = _svc(s)
        periodo = await svc.obtener_periodo(pid)
        detalle = await SqlNominaRepository(s).detalle_de(pid, tid)
        # Anclas concretas: salario proporcional 15/30, auxilio proporcional (salario ≤ 2 SMMLV), deducciones 4%+4%.
        assert detalle.salario_devengado == Decimal("750000")
        assert detalle.auxilio_transporte == Decimal("124547.50")
        assert detalle.valor_horas_extra == Decimal("0")
        assert detalle.total_deducciones == Decimal("60000")     # 30000 salud + 30000 pensión
        assert detalle.neto_pagar == Decimal("814547.50")
        assert detalle.dias_liquidados == Decimal("15")
        assert detalle.cune_dian is None                          # nómina electrónica = Fase 7

        # Fidelidad de persistencia: el detalle == motor puro con el MISMO snapshot congelado.
        esperado = liquidar_directo(
            _TrabDirecto(salario_base=Decimal("1500000")),
            _Asistencia(dias_trabajados=Decimal("15"), horas_extra_diurnas=Decimal("0"),
                        horas_extra_nocturnas=Decimal("0"), horas_dominicales=Decimal("0")),
            _snapshot_a_parametros(periodo),
        )
        assert detalle.total_devengado == esperado.total_devengado
        assert detalle.aportes_empleador == esperado.aportes_empleador
        assert detalle.provisiones == esperado.provisiones
        assert detalle.neto_pagar == esperado.neto_pagar


# --- flag aplica_aux_transporte (LOW): el DIRECTO que no lo aplica no recibe auxilio ---------
async def test_liquidar_directo_sin_aux_transporte(tenant):
    """Un DIRECTO con `aplica_aux_transporte=false` NO recibe auxilio, aunque su salario esté bajo el
    tope legal (el flag del trabajador manda además del tope por salario). El resto del devengado intacto."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        tid = (
            await s.execute(
                text(
                    "INSERT INTO trabajadores "
                    "(tipo_vinculacion, documento, nombres, apellidos, cargo, salario_base, aplica_aux_transporte) "
                    "VALUES ('DIRECTO', 'noaux', 'Ana', 'Ruiz', 'Operador', 1500000, false) RETURNING id"
                )
            )
        ).scalar_one()
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        await _asistencia(_svc(s), tid, 15)   # 15 días, salario bajo el tope (elegible por salario)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).liquidar_periodo(pid)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        detalle = await SqlNominaRepository(s).detalle_de(pid, tid)
        assert detalle.auxilio_transporte == Decimal("0")        # el flag lo anula
        assert detalle.salario_devengado == Decimal("750000")    # salario proporcional intacto
        assert detalle.total_devengado == Decimal("750000")      # sin el auxilio en el devengado
        assert detalle.total_deducciones == Decimal("60000")     # base sin auxilio: 750000 × (4%+4%)
        assert detalle.neto_pagar == Decimal("690000")           # 750000 − 60000 (sin el auxilio)


# --- clave natural de asistencia (LOW): re-registrar el mismo día es idempotente --------------
async def test_registrar_asistencia_mismo_dia_es_idempotente(tenant):
    """Dos altas del MISMO (trabajador, fecha) dejan UNA sola fila (UPSERT por la clave natural de 0051):
    la 2ª corrige la 1ª en vez de duplicar —dos filas inflarían `dias_trabajados` en la liquidación."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        tid = await _seed_trabajador(s, documento="idem", salario="1500000")
        await s.commit()
        svc = _svc(s)
        await svc.registrar_asistencia(
            AsistenciaCrear(trabajador_id=tid, fecha=_INICIO, horas_trabajadas=Decimal("8"))
        )
        r2 = await svc.registrar_asistencia(
            AsistenciaCrear(trabajador_id=tid, fecha=_INICIO, horas_trabajadas=Decimal("6"))
        )
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM registros_asistencia WHERE trabajador_id=:t"), {"t": tid}
            )
        ).scalar_one()
        horas = (
            await s.execute(
                text("SELECT horas_trabajadas FROM registros_asistencia WHERE trabajador_id=:t"), {"t": tid}
            )
        ).scalar_one()
    assert n == 1                          # una sola fila (idempotente por trabajador_id+fecha)
    assert horas == Decimal("6")           # la 2ª alta ACTUALIZÓ (no duplicó)
    assert r2.horas_trabajadas == Decimal("6")


# --- liquidación PATACALIENTE (acceptance) -----------------------------------
async def test_liquidar_patacaliente_48h(tenant):
    """PATACALIENTE 48 h × 12.000 = 576.000; sin deducciones, aportes ni provisiones (spec 08)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        tid = await _seed_trabajador(s, tipo="PATACALIENTE", documento="p1", tarifa="12000")
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        await _asistencia(_svc(s), tid, 6, horas=8)   # 6 días × 8 h = 48 h
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).liquidar_periodo(pid)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        detalle = await SqlNominaRepository(s).detalle_de(pid, tid)
        assert detalle.tipo_vinculacion == "PATACALIENTE"
        assert detalle.total_devengado == Decimal("576000")
        assert detalle.neto_pagar == Decimal("576000")
        assert detalle.total_deducciones == Decimal("0")
        assert detalle.aportes_empleador == Decimal("0")
        assert detalle.provisiones == Decimal("0")


# --- INVARIANTE conciliación del prorrateo -----------------------------------
async def test_prorrateo_concilia_exacto(tenant):
    """15 días (10 obra A, 3 obra B, 2 admin): 3 filas de prorrateo que suman EXACTO el costo total."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        cid = await _seed_cliente(s)
        obra_a = await _seed_obra(s, cid, "Obra A")
        obra_b = await _seed_obra(s, cid, "Obra B")
        tid = await _seed_trabajador(s, documento="d2", salario="1500000")
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        svc = _svc(s)
        await _asistencia(svc, tid, 10, obra_id=obra_a, desde=_INICIO)
        await _asistencia(svc, tid, 3, obra_id=obra_b, desde=_INICIO + timedelta(days=10))
        await _asistencia(svc, tid, 2, obra_id=None, desde=_INICIO + timedelta(days=13))
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).liquidar_periodo(pid)
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        detalle = await SqlNominaRepository(s).detalle_de(pid, tid)
        costo_total = detalle.total_devengado + detalle.aportes_empleador + detalle.provisiones
        filas = (
            await s.execute(
                text(
                    "SELECT obra_id, dias_imputados, costo_imputado FROM prorrateo_nomina_obra "
                    "WHERE periodo_id=:p AND trabajador_id=:t ORDER BY obra_id NULLS LAST"
                ),
                {"p": pid, "t": tid},
            )
        ).all()
        assert len(filas) == 3
        suma = sum((c for _, _, c in filas), Decimal("0"))
        assert suma == costo_total                              # conciliación EXACTA, sin residuo
        assert sum((d for _, d, _ in filas), Decimal("0")) == Decimal("15")   # días cuadran
        obras_presentes = {oid for oid, _, _ in filas}
        assert obra_a in obras_presentes and obra_b in obras_presentes and None in obras_presentes


# --- INVARIANTE idempotencia -------------------------------------------------
async def test_reliquidar_es_idempotente(tenant):
    """Re-liquidar recomputa sin duplicar: mismo número de detalles/prorrateos y mismos valores."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        cid = await _seed_cliente(s)
        obra_a = await _seed_obra(s, cid, "Obra A")
        tid = await _seed_trabajador(s, documento="d3", salario="1500000")
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        await _asistencia(_svc(s), tid, 10, obra_id=obra_a)
        await s.commit()

    async def _liquidar():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            r = await _svc(s).liquidar_periodo(pid)
            await s.commit()
            return r

    async def _conteos():
        async with AsyncSession(tenant.engine) as s:
            det = (
                await s.execute(
                    text("SELECT count(*) FROM detalles_liquidacion WHERE periodo_id=:p"), {"p": pid}
                )
            ).scalar_one()
            pro = (
                await s.execute(
                    text("SELECT count(*) FROM prorrateo_nomina_obra WHERE periodo_id=:p"), {"p": pid}
                )
            ).scalar_one()
            neto = (
                await s.execute(
                    text("SELECT neto_pagar FROM detalles_liquidacion WHERE periodo_id=:p"), {"p": pid}
                )
            ).scalar_one()
            det_id = (
                await s.execute(
                    text("SELECT id FROM detalles_liquidacion WHERE periodo_id=:p"), {"p": pid}
                )
            ).scalar_one()
            return det, pro, neto, det_id

    r1 = await _liquidar()
    c1 = await _conteos()
    r2 = await _liquidar()
    c2 = await _conteos()

    assert (c1[0], c1[1]) == (1, 1)               # un detalle, un prorrateo
    assert c2[:3] == c1[:3]                        # mismos conteos y mismo neto tras re-liquidar
    assert c2[3] == c1[3]                          # el detalle es el MISMO registro (UPSERT, no otro)
    assert r1.total_costo == r2.total_costo


async def test_cerrar_y_pagar_idempotentes(tenant):
    """Cerrar/pagar dos veces = replay; tras cerrar no se re-liquida; pagar exige estar cerrado."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        tid = await _seed_trabajador(s, documento="d4", salario="1500000")
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        await _asistencia(_svc(s), tid, 10)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).liquidar_periodo(pid)
        await s.commit()

    # pagar antes de cerrar → 409
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PeriodoBloqueado):
            await _svc(s).pagar_periodo(pid)
        await s.rollback()

    # cerrar (x2 = replay)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).cerrar_periodo(pid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).cerrar_periodo(pid)
        await s.commit()
    assert r1.replay is False and r2.replay is True
    assert r1.estado == "LIQUIDADO"

    # tras cerrar, re-liquidar está bloqueado
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PeriodoBloqueado):
            await _svc(s).liquidar_periodo(pid)
        await s.rollback()

    # pagar (x2 = replay)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        p1 = await _svc(s).pagar_periodo(pid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        p2 = await _svc(s).pagar_periodo(pid)
        await s.commit()
    assert p1.replay is False and p2.replay is True
    assert p1.estado == "PAGADO"


# --- INVARIANTE idempotencia: re-liquidar purga a quien ya no liquida (MEDIUM-1) --------------
async def test_reliquidar_purga_trabajador_sin_actividad(tenant):
    """Re-liquidar es un REEMPLAZO atómico del set liquidado: si un trabajador queda con 0 días, su
    detalle y su prorrateo VIEJOS deben desaparecer y NO seguir inflando los totales del periodo ni el
    costo de obra. Un segundo trabajador que sí trabaja debe sobrevivir intacto.

    Sin el fix, el trabajador sin actividad hace `continue` y sus filas huérfanas persisten: el total
    persistido del periodo queda inflado (X+Y) y su detalle sigue presente."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        cid = await _seed_cliente(s)
        obra_a = await _seed_obra(s, cid, "Obra A")
        xid = await _seed_trabajador(s, documento="m1-x", salario="1500000")
        yid = await _seed_trabajador(s, documento="m1-y", salario="1500000")
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        svc = _svc(s)
        await _asistencia(svc, xid, 15, obra_id=obra_a)
        await _asistencia(svc, yid, 10, obra_id=obra_a)
        await s.commit()

    # primera liquidación: X e Y quedan con detalle + prorrateo
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).liquidar_periodo(pid)
        await s.commit()
        assert r1.trabajadores_liquidados == 2

    async def _totales_periodo():
        async with AsyncSession(tenant.engine) as s:
            costo_det = (
                await s.execute(
                    text(
                        "SELECT COALESCE(SUM(total_devengado + aportes_empleador + provisiones), 0) "
                        "FROM detalles_liquidacion WHERE periodo_id=:p"
                    ),
                    {"p": pid},
                )
            ).scalar_one()
            costo_pro = (
                await s.execute(
                    text(
                        "SELECT COALESCE(SUM(costo_imputado), 0) FROM prorrateo_nomina_obra "
                        "WHERE periodo_id=:p"
                    ),
                    {"p": pid},
                )
            ).scalar_one()
            return costo_det, costo_pro

    costo_det_2, _ = await _totales_periodo()
    assert costo_det_2 > 0

    # X pierde toda su actividad (0 días): se borran sus registros de asistencia
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await s.execute(text("DELETE FROM registros_asistencia WHERE trabajador_id=:t"), {"t": xid})
        await s.commit()

    # re-liquidar: X debe salir del periodo por completo, Y intacto
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).liquidar_periodo(pid)
        await s.commit()
        assert r2.trabajadores_liquidados == 1   # solo Y

    async with AsyncSession(tenant.engine) as s:
        repo = SqlNominaRepository(s)
        assert await repo.detalle_de(pid, xid) is None            # X purgado
        assert await repo.detalle_de(pid, yid) is not None        # Y sobrevive
        n_pro_x = (
            await s.execute(
                text(
                    "SELECT count(*) FROM prorrateo_nomina_obra WHERE periodo_id=:p AND trabajador_id=:t"
                ),
                {"p": pid, "t": xid},
            )
        ).scalar_one()
        assert n_pro_x == 0                                       # prorrateo de X purgado

    # los totales persistidos del periodo NO incluyen a X: quedan iguales al costo que reporta el resumen
    costo_det_final, costo_pro_final = await _totales_periodo()
    assert costo_det_final == r2.total_costo                      # solo Y, sin la inflación de X
    assert costo_pro_final == r2.total_costo                      # costo de obra tampoco inflado
    assert costo_det_final < costo_det_2                          # bajó respecto a cuando estaban X e Y


# --- INVARIANTE idempotencia: upsert atómico del detalle bajo concurrencia (MEDIUM-3) ---------
async def test_upsert_detalle_concurrente_no_colisiona(tenant):
    """Dos liquidaciones concurrentes del MISMO (periodo, trabajador) chocan en el
    UNIQUE(periodo_id, trabajador_id). Sin el upsert atómico (SELECT-then-INSERT), la perdedora
    revienta con IntegrityError (500). Con INSERT ... ON CONFLICT DO UPDATE ambas resuelven y queda
    EXACTAMENTE un detalle."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        tid = await _seed_trabajador(s, documento="m3-1", salario="1500000")
        periodo = await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()
        pid = periodo.id
        # Liquidación real con el snapshot congelado del periodo (misma para ambas corridas).
        liq = liquidar_directo(
            _TrabDirecto(salario_base=Decimal("1500000")),
            _Asistencia(dias_trabajados=Decimal("15"), horas_extra_diurnas=Decimal("0"),
                        horas_extra_nocturnas=Decimal("0"), horas_dominicales=Decimal("0")),
            _snapshot_a_parametros(periodo),
        )
    ahora = now_co()

    async def _upsert() -> str:
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            repo = SqlNominaRepository(s)
            try:
                await repo.upsert_detalle(
                    pid, tid, tipo_vinculacion="DIRECTO", dias=Decimal("15"), liq=liq, ahora=ahora
                )
                await s.commit()
                return "ok"
            except IntegrityError:
                await s.rollback()
                return "conflicto"

    resultados = sorted(await asyncio.gather(_upsert(), _upsert()))
    assert resultados == ["ok", "ok"]   # el upsert atómico convierte la colisión en UPDATE

    async with AsyncSession(tenant.engine) as s:
        n = (
            await s.execute(
                text(
                    "SELECT count(*) FROM detalles_liquidacion WHERE periodo_id=:p AND trabajador_id=:t"
                ),
                {"p": pid, "t": tid},
            )
        ).scalar_one()
        assert n == 1   # un solo detalle pese a las dos liquidaciones concurrentes


# --- aislamiento multi-tenant ------------------------------------------------
async def test_empresa_A_no_ve_periodos_de_B(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await _seed_parametros(s)
        await _svc(s).crear_periodo(_periodo_quincena())
        await s.commit()

    async with AsyncSession(a.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM periodos_nomina"))).scalar_one() == 1
    async with AsyncSession(b.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM periodos_nomina"))).scalar_one() == 0
