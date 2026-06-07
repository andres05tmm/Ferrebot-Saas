"""Integración del motor del pack Agenda/Citas contra una base efímera real (fixture `tenant`).

Cubre agendar (auto/manual + idempotencia), cupo ocupado con alternativas, resta de la cita en la
disponibilidad, reagendar, cancelar con `politica_cancelacion_horas`, el guardarraíl por teléfono y
la CONCURRENCIA (dos reservas del mismo cupo → solo una gana).
"""
from datetime import datetime, time, timedelta

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import COLOMBIA_TZ, now_co, today_co
from modules.agenda.errors import (
    CitaInexistente,
    CupoNoDisponible,
    FueraDePoliticaCancelacion,
    ReagendarNoPermitido,
)
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import (
    AgendaConfigCrear,
    BloqueoCrear,
    CitaCrear,
    DisponibilidadCrear,
    RecursoCrear,
    ServicioCrear,
)
from modules.agenda.service import AgendaService

TEL = "3001234567"


def _futuro(dias: int = 3, hora: int = 10, minuto: int = 0) -> datetime:
    """Un instante futuro en hora Colombia, sobre la rejilla de 30 min (no flaky por la hora actual)."""
    d = today_co() + timedelta(days=dias)
    return datetime.combine(d, time(hora, minuto), tzinfo=COLOMBIA_TZ)


def _svc(s: AsyncSession) -> AgendaService:
    return AgendaService(SqlAgendaRepository(s))


async def _seed(
    s: AsyncSession,
    *,
    modo: str = "auto",
    anticipacion: int = 0,
    ventana: int = 60,
    intervalo: int = 30,
    politica: int = 24,
    permite_reagendar: bool = True,
    duracion: int = 30,
    capacidad: int = 1,
) -> tuple[int, int]:
    """Crea servicio + recurso + disponibilidad (toda la semana 08–18) + config; devuelve (serv, rec)."""
    repo = SqlAgendaRepository(s)
    serv = await repo.crear_servicio(ServicioCrear(nombre="Limpieza", duracion_min=duracion))
    rec = await repo.crear_recurso(RecursoCrear(nombre="Dra. Pérez", tipo="profesional"))
    await repo.asignar_servicio(recurso_id=rec.id, servicio_id=serv.id)
    for dia in range(7):
        await repo.crear_disponibilidad(
            DisponibilidadCrear(recurso_id=rec.id, dia_semana=dia, hora_inicio=time(8), hora_fin=time(18))
        )
    await repo.guardar_config(
        AgendaConfigCrear(
            modo_confirmacion=modo,
            anticipacion_minima_min=anticipacion,
            ventana_maxima_dias=ventana,
            intervalo_slots_min=intervalo,
            politica_cancelacion_horas=politica,
            permite_reagendar=permite_reagendar,
            capacidad_por_slot=capacidad,
        )
    )
    await s.commit()
    return serv.id, rec.id


# --- agendar ----------------------------------------------------------------
async def test_agendar_auto_confirma_y_es_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s, modo="auto")
        svc = _svc(s)
        inicio = _futuro(hora=10)
        r1 = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=inicio,
            cliente_nombre="Andrés", cliente_telefono=TEL, idempotency_key="k-1",
        )
        await s.commit()
        assert r1.replay is False
        assert r1.cita.estado == "confirmada"
        assert r1.cita.fin == inicio + timedelta(minutes=30)

        # Misma idempotency_key → replay, sin duplicar.
        r2 = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=inicio,
            cliente_nombre="Andrés", cliente_telefono=TEL, idempotency_key="k-1",
        )
        await s.commit()
        assert r2.replay is True
        assert r2.cita.id == r1.cita.id


async def test_agendar_modo_manual_queda_pendiente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s, modo="manual")
        r = await _svc(s).validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=11),
            cliente_nombre="Andrés", cliente_telefono=TEL,
        )
        await s.commit()
        assert r.cita.estado == "pendiente"


async def test_cupo_ocupado_lanza_con_alternativas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        svc = _svc(s)
        inicio = _futuro(hora=10)
        await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=inicio,
            cliente_nombre="A", cliente_telefono=TEL, idempotency_key="ka",
        )
        await s.commit()
        with pytest.raises(CupoNoDisponible) as ex:
            await svc.validar_y_agendar(
                servicio_id=serv, recurso_id=rec, inicio=inicio,
                cliente_nombre="B", cliente_telefono="3009999999", idempotency_key="kb",
            )
        assert ex.value.alternativas  # ofrece otros cupos
        assert inicio not in ex.value.alternativas


# --- disponibilidad ---------------------------------------------------------
async def test_disponibilidad_resta_la_cita_agendada(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s, intervalo=30, duracion=30)
        svc = _svc(s)
        dia = _futuro(hora=10).date()
        inicio = _futuro(hora=10)
        antes = {x.inicio for x in await svc.calcular_disponibilidad(serv, desde=dia, hasta=dia, recurso_id=rec)}
        assert inicio in antes

        await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=inicio,
            cliente_nombre="A", cliente_telefono=TEL, idempotency_key="kx",
        )
        await s.commit()
        despues = {x.inicio for x in await svc.calcular_disponibilidad(serv, desde=dia, hasta=dia, recurso_id=rec)}
        assert inicio not in despues                       # el cupo ocupado desaparece
        assert _futuro(hora=10, minuto=30) in despues      # el vecino sigue libre


# --- reagendar --------------------------------------------------------------
async def test_reagendar_mueve_y_rechaza_cupo_ocupado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        svc = _svc(s)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="A", cliente_telefono=TEL, idempotency_key="k1",
        )
        await s.commit()

        movida = await svc.reagendar(r.cita.id, _futuro(hora=11))
        await s.commit()
        assert movida.inicio == _futuro(hora=11)

        # Ocupar las 12:00 y tratar de reagendar la cita allí → choca.
        await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=12),
            cliente_nombre="B", cliente_telefono="3000000000", idempotency_key="k2",
        )
        await s.commit()
        with pytest.raises(CupoNoDisponible):
            await svc.reagendar(r.cita.id, _futuro(hora=12))


async def test_reagendar_bloqueado_si_negocio_no_lo_permite(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s, permite_reagendar=False)
        svc = _svc(s)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="A", cliente_telefono=TEL, idempotency_key="k1",
        )
        await s.commit()
        with pytest.raises(ReagendarNoPermitido):
            await svc.reagendar(r.cita.id, _futuro(hora=11))


# --- cancelar + política ----------------------------------------------------
async def test_cancelar_respeta_politica_de_cancelacion(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s, politica=24)
        repo = SqlAgendaRepository(s)
        svc = _svc(s)

        # Cita lejana (>24 h): se cancela.
        lejana = now_co() + timedelta(hours=72)
        c_ok = await repo.crear_cita(
            CitaCrear(servicio_id=serv, recurso_id=rec, cliente_nombre="A", cliente_telefono=TEL,
                      inicio=lejana, fin=lejana + timedelta(minutes=30)),
            estado="confirmada", fin=lejana + timedelta(minutes=30),
        )
        # Cita inminente (<24 h): la política la protege.
        pronto = now_co() + timedelta(hours=2)
        c_no = await repo.crear_cita(
            CitaCrear(servicio_id=serv, recurso_id=rec, cliente_nombre="B", cliente_telefono=TEL,
                      inicio=pronto, fin=pronto + timedelta(minutes=30)),
            estado="confirmada", fin=pronto + timedelta(minutes=30),
        )
        await s.commit()

        cancelada = await svc.cancelar(c_ok.id)
        await s.commit()
        assert cancelada.estado == "cancelada"

        with pytest.raises(FueraDePoliticaCancelacion):
            await svc.cancelar(c_no.id)


async def test_guardarrail_por_telefono_no_toca_citas_ajenas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        svc = _svc(s)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="A", cliente_telefono=TEL, idempotency_key="k1",
        )
        await s.commit()
        with pytest.raises(CitaInexistente):
            await svc.cancelar(r.cita.id, telefono="3990000000")
        with pytest.raises(CitaInexistente):
            await svc.reagendar(r.cita.id, _futuro(hora=11), telefono="3990000000")


# --- bloqueos ---------------------------------------------------------------
@pytest.mark.parametrize("recurso_global", [False, True])
async def test_bloqueo_resta_disponibilidad(tenant, recurso_global):
    """Un bloqueo (del recurso o global con recurso_id=NULL) borra los cupos que solapa."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s, intervalo=30, duracion=30)
        repo = SqlAgendaRepository(s)
        await repo.crear_bloqueo(
            BloqueoCrear(
                recurso_id=None if recurso_global else rec,
                inicio=_futuro(hora=10), fin=_futuro(hora=11), motivo="almuerzo",
            )
        )
        await s.commit()
        dia = _futuro(hora=10).date()
        libres = {x.inicio for x in await _svc(s).calcular_disponibilidad(serv, desde=dia, hasta=dia, recurso_id=rec)}
        assert _futuro(hora=10) not in libres
        assert _futuro(hora=10, minuto=30) not in libres
        assert _futuro(hora=9, minuto=30) in libres   # antes del bloqueo
        assert _futuro(hora=11) in libres             # toca el fin del bloqueo: libre


# --- listados ---------------------------------------------------------------
async def test_listados_y_citas_de_cliente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        svc = _svc(s)
        repo = SqlAgendaRepository(s)
        await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="A", cliente_telefono=TEL, idempotency_key="k1",
        )
        await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=11),
            cliente_nombre="A", cliente_telefono=TEL, idempotency_key="k2",
        )
        await s.commit()
        assert [x.nombre for x in await repo.listar_servicios()] == ["Limpieza"]
        assert [x.nombre for x in await repo.listar_recursos()] == ["Dra. Pérez"]
        mias = await repo.citas_de_cliente(TEL)
        assert len(mias) == 2
        assert mias[0].inicio < mias[1].inicio          # ordenadas, próximas primero
        assert await repo.citas_de_cliente("3990000000") == []  # solo las del teléfono que pide


# --- concurrencia -----------------------------------------------------------
async def test_concurrencia_dos_reservas_mismo_cupo_solo_una_gana(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
    inicio = _futuro(hora=10)

    async def _intento(key: str) -> str:
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            svc = _svc(s)
            try:
                await svc.validar_y_agendar(
                    servicio_id=serv, recurso_id=rec, inicio=inicio,
                    cliente_nombre=key, cliente_telefono=TEL, idempotency_key=key,
                )
                await s.commit()
                return "ok"
            except CupoNoDisponible:
                await s.rollback()
                return "conflicto"

    resultados = sorted(await asyncio.gather(_intento("c-1"), _intento("c-2")))
    assert resultados == ["conflicto", "ok"]  # el advisory lock serializa: solo una entra

    # En la base quedó exactamente una cita para ese cupo.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        citas = await SqlAgendaRepository(s).citas_de_recurso(
            recurso_id=rec, inicio=inicio, fin=inicio + timedelta(minutes=30)
        )
        assert len(citas) == 1
