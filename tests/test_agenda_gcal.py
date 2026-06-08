"""Sync OPCIONAL con Google Calendar del pack Agenda (write-only), con el cliente de Google MOCKEADO.

Cubre el contrato del enganche (sobre una base efímera real, fixture `tenant`):
- agendar con sync activo → crea el evento espejo y guarda `gcal_event_id` en la cita (persistido).
- cancelar → borra el evento y limpia `gcal_event_id`; reagendar → actualiza el evento.
- sync apagado (`google_calendar_id` NULL) → NO se llama a Google y `gcal_event_id` queda NULL.
- fallo de Google → la cita NO falla (best-effort): se crea igual, sin `gcal_event_id`.
- el evento lleva título (servicio + cliente), descripción (recurso + teléfono) y horas Colombia.

El cliente real (`GoogleCalendarClient`) no se ejercita aquí: se inyecta un fake del puerto `CalendarPort`.
"""
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import COLOMBIA_TZ, now_co, today_co
from modules.agenda.gcal import CalendarPort, EventoCalendario, evento_de_cita
from modules.agenda.models import Cita
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import (
    AgendaConfigCrear,
    CitaCrear,
    DisponibilidadCrear,
    RecursoCrear,
    ServicioCrear,
)
from modules.agenda.service import AgendaService

TEL = "3001234567"
CAL_ID = "negocio@group.calendar.google.com"


class FakeCalendar:
    """Puerto de calendario falso: registra las llamadas y devuelve ids deterministas."""

    def __init__(self, *, falla_crear: bool = False) -> None:
        self.creados: list[tuple[str, EventoCalendario]] = []
        self.actualizados: list[tuple[str, str, EventoCalendario]] = []
        self.borrados: list[tuple[str, str]] = []
        self._falla_crear = falla_crear
        self._n = 0

    async def crear_evento(self, calendar_id: str, evento: EventoCalendario) -> str:
        if self._falla_crear:
            raise RuntimeError("google caído")
        self._n += 1
        self.creados.append((calendar_id, evento))
        return f"gcal-evt-{self._n}"

    async def actualizar_evento(
        self, calendar_id: str, event_id: str, evento: EventoCalendario
    ) -> None:
        self.actualizados.append((calendar_id, event_id, evento))

    async def borrar_evento(self, calendar_id: str, event_id: str) -> None:
        self.borrados.append((calendar_id, event_id))


# El fake satisface el puerto estructural (sanity check, no afecta el runtime).
assert isinstance(FakeCalendar(), CalendarPort)


# --- builder del evento (puro, sin BD) --------------------------------------
class _Cita:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_evento_de_cita_formato_profesional():
    """El builder arma título, descripción estructurada (con link wa.me), estado, color y zona Colombia."""
    cita = _Cita(
        cliente_nombre="Andrés Felipe Malo", cliente_telefono="573001234567",
        estado="confirmada", confirmacion="esperando",
        inicio=datetime(2026, 6, 12, 14, 0, tzinfo=COLOMBIA_TZ),
        fin=datetime(2026, 6, 12, 14, 40, tzinfo=COLOMBIA_TZ),
    )
    servicio = _Cita(nombre="Limpieza dental", duracion_min=40)
    recurso = _Cita(nombre="Dra. Pérez")

    e = evento_de_cita(cita, servicio, recurso)
    assert e.titulo == "Limpieza dental · Andrés Felipe Malo"
    desc = e.descripcion
    assert "Cliente: Andrés Felipe Malo" in desc
    assert "WhatsApp: +57 300 123 4567 — https://wa.me/573001234567" in desc
    assert "Servicio: Limpieza dental (40 min)" in desc
    assert "Profesional: Dra. Pérez" in desc
    assert "Estado: Esperando confirmación" in desc
    assert "Agendado por el asistente vía WhatsApp" in desc

    body = e.to_body()
    assert body["colorId"] == "9"   # esperando = azul
    assert body["start"]["timeZone"] == "America/Bogota"
    assert body["start"]["dateTime"].endswith("-05:00")
    assert "location" not in body  # sin dirección configurada


def test_evento_color_y_estado_por_confirmacion():
    """El color y la línea Estado reflejan el sub-estado: reconfirmada=verde, en_riesgo=rojo/ámbar."""
    base = dict(
        cliente_nombre="Ana", cliente_telefono="573001234567",
        inicio=datetime(2026, 6, 12, 14, 0, tzinfo=COLOMBIA_TZ),
        fin=datetime(2026, 6, 12, 14, 30, tzinfo=COLOMBIA_TZ),
    )
    servicio = _Cita(nombre="Corte", duracion_min=30)
    recurso = _Cita(nombre="Dra. Pérez")

    recon = evento_de_cita(_Cita(estado="confirmada", confirmacion="reconfirmada", **base), servicio, recurso)
    assert recon.to_body()["colorId"] == "10"          # verde (Basil)
    assert "Estado: Reconfirmada" in recon.descripcion

    riesgo = evento_de_cita(_Cita(estado="confirmada", confirmacion="en_riesgo", **base), servicio, recurso)
    assert riesgo.to_body()["colorId"] == "11"         # rojo/ámbar (Tomato)
    assert "Estado: En riesgo" in riesgo.descripcion


def test_evento_de_cita_sin_recurso_y_con_direccion():
    """Sin recurso se omite el profesional; con dirección va en location."""
    cita = _Cita(
        cliente_nombre="Ana", cliente_telefono="3009998877",
        estado="confirmada", confirmacion="esperando",
        inicio=datetime(2026, 6, 12, 9, 0, tzinfo=COLOMBIA_TZ),
        fin=datetime(2026, 6, 12, 9, 30, tzinfo=COLOMBIA_TZ),
    )
    servicio = _Cita(nombre="Corte", duracion_min=30)

    e = evento_de_cita(cita, servicio, None, direccion="Cra 1 # 2-3, Cartagena")
    assert "Profesional:" not in e.descripcion
    assert "Estado: Esperando confirmación" in e.descripcion
    assert "https://wa.me/3009998877" in e.descripcion           # móvil local de 10 dígitos
    assert e.to_body()["location"] == "Cra 1 # 2-3, Cartagena"


def _futuro(dias: int = 3, hora: int = 10, minuto: int = 0) -> datetime:
    d = today_co() + timedelta(days=dias)
    return datetime.combine(d, time(hora, minuto), tzinfo=COLOMBIA_TZ)


async def _seed(s: AsyncSession, *, calendar_id: str | None = CAL_ID) -> tuple[int, int]:
    """Servicio + recurso + disponibilidad semana 08–18 + config (con o sin calendar_id)."""
    repo = SqlAgendaRepository(s)
    serv = await repo.crear_servicio(ServicioCrear(nombre="Limpieza", duracion_min=30))
    rec = await repo.crear_recurso(RecursoCrear(nombre="Dra. Pérez", tipo="profesional"))
    await repo.asignar_servicio(recurso_id=rec.id, servicio_id=serv.id)
    for dia in range(7):
        await repo.crear_disponibilidad(
            DisponibilidadCrear(recurso_id=rec.id, dia_semana=dia, hora_inicio=time(8), hora_fin=time(18))
        )
    await repo.guardar_config(
        AgendaConfigCrear(
            anticipacion_minima_min=0, ventana_maxima_dias=60, intervalo_slots_min=30,
            politica_cancelacion_horas=0, google_calendar_id=calendar_id,
        )
    )
    await s.commit()
    return serv.id, rec.id


async def test_agendar_crea_evento_y_guarda_gcal_event_id(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        fake = FakeCalendar()
        svc = AgendaService(SqlAgendaRepository(s), gcal=fake)
        inicio = _futuro(hora=10)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=inicio,
            cliente_nombre="Andrés", cliente_telefono=TEL, idempotency_key="k-1",
        )
        await s.commit()

        assert len(fake.creados) == 1
        calendar_id, evento = fake.creados[0]
        assert calendar_id == CAL_ID
        assert evento.titulo == "Limpieza · Andrés"
        assert "Profesional: Dra. Pérez" in evento.descripcion
        assert TEL in evento.descripcion  # aparece en el link wa.me
        assert evento.inicio.utcoffset() == timedelta(hours=-5)  # hora Colombia
        assert r.cita.gcal_event_id == "gcal-evt-1"
        cita_id = r.cita.id

    # Persistido: una sesión nueva trae el id guardado.
    async with AsyncSession(tenant.engine) as s2:
        recargada = await s2.get(Cita, cita_id)
        assert recargada.gcal_event_id == "gcal-evt-1"


async def test_cancelar_borra_evento_y_limpia_id(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        fake = FakeCalendar()
        svc = AgendaService(SqlAgendaRepository(s), gcal=fake)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="Andrés", cliente_telefono=TEL, idempotency_key="k-1",
        )
        await s.commit()

        cancelada = await svc.cancelar(r.cita.id, telefono=TEL)
        await s.commit()

        assert fake.borrados == [(CAL_ID, "gcal-evt-1")]
        assert cancelada.gcal_event_id is None


async def test_reagendar_actualiza_evento(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        fake = FakeCalendar()
        svc = AgendaService(SqlAgendaRepository(s), gcal=fake)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="Andrés", cliente_telefono=TEL, idempotency_key="k-1",
        )
        await s.commit()

        nuevo = _futuro(hora=12)
        movida = await svc.reagendar(r.cita.id, nuevo, telefono=TEL)
        await s.commit()

        assert len(fake.actualizados) == 1
        calendar_id, event_id, evento = fake.actualizados[0]
        assert (calendar_id, event_id) == (CAL_ID, "gcal-evt-1")
        assert evento.inicio.hour == 12
        assert movida.gcal_event_id == "gcal-evt-1"  # mismo evento, no se duplica


async def test_dashboard_cancelar_negocio_borra_evento(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        fake = FakeCalendar()
        svc = AgendaService(SqlAgendaRepository(s), gcal=fake)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="Andrés", cliente_telefono=TEL, origen="dashboard",
        )
        await s.commit()
        assert len(fake.creados) == 1  # el alta manual también espeja

        await svc.cancelar_negocio(r.cita.id)
        await s.commit()
        assert fake.borrados == [(CAL_ID, "gcal-evt-1")]


async def test_sync_apagado_no_llama_a_google(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s, calendar_id=None)  # sin calendar_id → sync apagado
        fake = FakeCalendar()
        svc = AgendaService(SqlAgendaRepository(s), gcal=fake)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="Andrés", cliente_telefono=TEL,
        )
        await s.commit()

        assert fake.creados == []
        assert r.cita.gcal_event_id is None


async def test_sin_cliente_gcal_no_rompe(tenant):
    """Sin cliente de plataforma (gcal=None), aunque el tenant tenga calendar_id, no se sincroniza."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        svc = AgendaService(SqlAgendaRepository(s))  # gcal=None
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="Andrés", cliente_telefono=TEL,
        )
        await s.commit()
        assert r.cita.gcal_event_id is None


async def test_fallo_de_google_no_rompe_la_cita(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        fake = FakeCalendar(falla_crear=True)
        svc = AgendaService(SqlAgendaRepository(s), gcal=fake)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="Andrés", cliente_telefono=TEL, idempotency_key="k-1",
        )
        await s.commit()

        # La cita existe y quedó firme; solo no tiene espejo en Google.
        assert r.cita.id is not None
        assert r.cita.estado == "confirmada"
        cita_id = r.cita.id

    async with AsyncSession(tenant.engine) as s2:
        recargada = await s2.get(Cita, cita_id)
        assert recargada is not None
        assert recargada.gcal_event_id is None


# --- sub-estado de reconfirmación: color en Calendar ------------------------
async def _enviar_ok(cita) -> bool:
    return True


async def test_reconfirmar_actualiza_evento_a_verde(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        fake = FakeCalendar()
        svc = AgendaService(SqlAgendaRepository(s), gcal=fake)
        r = await svc.validar_y_agendar(
            servicio_id=serv, recurso_id=rec, inicio=_futuro(hora=10),
            cliente_nombre="Andrés", cliente_telefono=TEL, idempotency_key="k-1",
        )
        await s.commit()

        cita = await svc.reconfirmar(r.cita.id, telefono=TEL)
        await s.commit()

    assert cita.confirmacion == "reconfirmada"
    assert len(fake.actualizados) == 1
    calendar_id, event_id, evento = fake.actualizados[0]
    assert (calendar_id, event_id) == (CAL_ID, "gcal-evt-1")
    assert evento.to_body()["colorId"] == "10"          # verde
    assert "Estado: Reconfirmada" in evento.descripcion


async def test_flip_en_riesgo_actualiza_evento_a_rojo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)            # corte_riesgo_horas default 2, calendar activo
        repo = SqlAgendaRepository(s)
        ahora = now_co()
        inicio = ahora + timedelta(hours=1)   # dentro del corte de riesgo
        fin = inicio + timedelta(minutes=30)
        cita = await repo.crear_cita(
            CitaCrear(servicio_id=serv, recurso_id=rec, cliente_nombre="Ana",
                      cliente_telefono=TEL, inicio=inicio, fin=fin, origen="whatsapp"),
            estado="confirmada", fin=fin,
        )
        await repo.fijar_gcal_event_id(cita, "evt-x")   # ya tiene espejo en Google
        await s.commit()

        fake = FakeCalendar()
        svc = AgendaService(repo, gcal=fake)
        resumen = await svc.procesar_reconfirmaciones(ahora=ahora, enviar=_enviar_ok)
        await s.commit()

    assert resumen.en_riesgo == 1
    rojos = [ev for (_, eid, ev) in fake.actualizados if eid == "evt-x"]
    assert rojos and rojos[0].to_body()["colorId"] == "11"   # rojo/ámbar
    assert "Estado: En riesgo" in rojos[0].descripcion
