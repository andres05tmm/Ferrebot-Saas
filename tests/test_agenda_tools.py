"""Herramientas de agente del pack Agenda (`ai/agenda_tools.py`) contra base efímera real.

Verifica que cada herramienta llama al motor y formatea la salida para el agente, que los errores de
dominio (CupoNoDisponible con alternativas) se propagan usables, y —lo crítico— el GUARDARRAÍL de
seguridad: el teléfono sale del Contexto del canal; el modelo no puede pasar otro ni tocar citas
ajenas.
"""
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.agenda_tools import AgendaDeps, ejecutar
from ai.envelope import Contexto, ErrorTool, Resultado
from core.config.timezone import COLOMBIA_TZ, now_co, today_co
from core.llm.base import ToolCall
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import (
    AgendaConfigCrear,
    CitaCrear,
    DisponibilidadCrear,
    RecursoCrear,
    ServicioCrear,
)
from modules.agenda.service import AgendaService

TEL_A = "3001112233"
TEL_B = "3009998877"


def _futuro(hora: int = 10, minuto: int = 0, dias: int = 3) -> datetime:
    d = today_co() + timedelta(days=dias)
    return datetime.combine(d, time(hora, minuto), tzinfo=COLOMBIA_TZ)


def _deps(s: AsyncSession) -> AgendaDeps:
    return AgendaDeps(agenda=AgendaService(SqlAgendaRepository(s)))


def _ctx(telefono: str | None = TEL_A) -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=0, rol="vendedor", origen="bot",
        capacidades=frozenset({"pack_agenda"}), cliente_telefono=telefono,
    )


def _call(herramienta: str, **arguments) -> ToolCall:
    return ToolCall(id="t", name=herramienta, arguments=arguments)


async def _seed(s: AsyncSession, *, modo: str = "auto") -> tuple[int, int]:
    repo = SqlAgendaRepository(s)
    serv = await repo.crear_servicio(ServicioCrear(nombre="Limpieza", duracion_min=30, precio="80000"))
    rec = await repo.crear_recurso(RecursoCrear(nombre="Dra. Pérez", tipo="profesional"))
    await repo.asignar_servicio(recurso_id=rec.id, servicio_id=serv.id)
    for dia in range(7):
        await repo.crear_disponibilidad(
            DisponibilidadCrear(recurso_id=rec.id, dia_semana=dia, hora_inicio=time(8), hora_fin=time(18))
        )
    await repo.guardar_config(
        AgendaConfigCrear(modo_confirmacion=modo, anticipacion_minima_min=0, ventana_maxima_dias=60,
                          intervalo_slots_min=30, politica_cancelacion_horas=24)
    )
    await s.commit()
    return serv.id, rec.id


# --- lectura ----------------------------------------------------------------
async def test_listar_servicios(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, _ = await _seed(s)
        r = await ejecutar(_call("listar_servicios"), _ctx(), _deps(s))
        assert isinstance(r, Resultado)
        assert r.data["servicios"][0]["id"] == serv
        assert "Limpieza" in r.resumen and "80000" in r.resumen


async def test_consultar_disponibilidad_y_servicio_invalido(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, _ = await _seed(s)
        dia = _futuro().date().isoformat()
        r = await ejecutar(
            _call("consultar_disponibilidad", servicio_id=serv, desde=dia, hasta=dia), _ctx(), _deps(s)
        )
        assert isinstance(r, Resultado)
        assert r.data["slots"]
        assert _futuro(hora=10).isoformat() in [slot["inicio"] for slot in r.data["slots"]]

        err = await ejecutar(_call("consultar_disponibilidad", servicio_id=99999), _ctx(), _deps(s))
        assert isinstance(err, ErrorTool)
        assert err.error == "servicio_no_encontrado" and err.recuperable


# --- agendar ----------------------------------------------------------------
async def test_agendar_usa_telefono_del_contexto_no_de_los_args(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, _ = await _seed(s, modo="auto")
        # El modelo intenta colar otro teléfono en los args: debe ignorarse (no está en el schema).
        r = await ejecutar(
            _call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=10).isoformat(),
                  nombre="Andrés", cliente_telefono=TEL_B),
            _ctx(TEL_A), _deps(s),
        )
        await s.commit()
        assert isinstance(r, Resultado)
        assert r.data["estado"] == "confirmada"
        assert r.idempotente == "aplicada"
        # En la base la cita quedó con el teléfono del CONTEXTO (TEL_A), no el de los args (TEL_B).
        tel = (await s.execute(text("SELECT cliente_telefono FROM citas WHERE id=:i"), {"i": r.data["cita_id"]})).scalar_one()
        assert tel == TEL_A


async def test_agendar_cupo_ocupado_propaga_alternativas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        inicio = _futuro(hora=10).isoformat()
        await ejecutar(_call("agendar_cita", servicio_id=serv, inicio=inicio, nombre="A", recurso_id=rec), _ctx(TEL_A), _deps(s))
        await s.commit()
        err = await ejecutar(
            _call("agendar_cita", servicio_id=serv, inicio=inicio, nombre="B", recurso_id=rec), _ctx(TEL_B), _deps(s)
        )
        assert isinstance(err, ErrorTool)
        assert err.error == "cupo_no_disponible" and err.recuperable
        assert "Alternativas" in err.detail


async def test_agendar_validacion_de_args(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed(s)
        err = await ejecutar(_call("agendar_cita", nombre="A"), _ctx(), _deps(s))  # falta servicio_id/inicio
        assert isinstance(err, ErrorTool)
        assert err.error == "validacion" and err.recuperable


# --- mis_citas + seguridad --------------------------------------------------
async def test_mis_citas_solo_del_telefono_del_contexto(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        await ejecutar(_call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=10).isoformat(), nombre="A", recurso_id=rec), _ctx(TEL_A), _deps(s))
        await ejecutar(_call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=11).isoformat(), nombre="B", recurso_id=rec), _ctx(TEL_B), _deps(s))
        await s.commit()

        # El modelo intenta pedir las citas de OTRO número en los args: se ignora, usa el del contexto.
        r = await ejecutar(_call("mis_citas", cliente_telefono=TEL_B), _ctx(TEL_A), _deps(s))
        assert isinstance(r, Resultado)
        assert len(r.data["citas"]) == 1
        assert r.data["citas"][0]["inicio"] == _futuro(hora=10).isoformat()  # solo la de A


async def test_seguridad_no_cancela_ni_reagenda_cita_ajena(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        ok = await ejecutar(_call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=10).isoformat(), nombre="A", recurso_id=rec), _ctx(TEL_A), _deps(s))
        await s.commit()
        cita_id = ok.data["cita_id"]

        # Otro cliente (TEL_B) intenta cancelar/reagendar la cita de A → no la ve.
        for tc in (_call("cancelar_cita", cita_id=cita_id),
                   _call("reagendar_cita", cita_id=cita_id, nuevo_inicio=_futuro(hora=12).isoformat())):
            err = await ejecutar(tc, _ctx(TEL_B), _deps(s))
            assert isinstance(err, ErrorTool)
            assert err.error == "cita_no_encontrada"

        # La cita de A sigue intacta (confirmada, en su horario original).
        fila = (await s.execute(text("SELECT estado, inicio FROM citas WHERE id=:i"), {"i": cita_id})).one()
        assert fila.estado == "confirmada"


# --- reagendar / cancelar (camino feliz) ------------------------------------
async def test_reagendar_y_cancelar_propias(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        ok = await ejecutar(_call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=10).isoformat(), nombre="A", recurso_id=rec), _ctx(TEL_A), _deps(s))
        await s.commit()
        cita_id = ok.data["cita_id"]

        r = await ejecutar(_call("reagendar_cita", cita_id=cita_id, nuevo_inicio=_futuro(hora=11).isoformat()), _ctx(TEL_A), _deps(s))
        await s.commit()
        assert isinstance(r, Resultado)
        assert r.data["inicio"] == _futuro(hora=11).isoformat()

        c = await ejecutar(_call("cancelar_cita", cita_id=cita_id), _ctx(TEL_A), _deps(s))
        await s.commit()
        assert isinstance(c, Resultado)
        assert c.data["estado"] == "cancelada"


async def _crear_cita_cercana(s: AsyncSession, serv: int, rec: int, telefono: str = TEL_A):
    """Inserta directamente una cita inminente (<24 h) para probar la política sin pasar por agendar."""
    pronto = now_co() + timedelta(hours=2)
    fin = pronto + timedelta(minutes=30)
    cita = await SqlAgendaRepository(s).crear_cita(
        CitaCrear(servicio_id=serv, recurso_id=rec, cliente_nombre="A", cliente_telefono=telefono,
                  inicio=pronto, fin=fin),
        estado="confirmada", fin=fin,
    )
    await s.commit()
    return cita.id


async def test_reagendar_propaga_errores_de_dominio(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        # Fuera de política (cita a 2 h, política 24 h).
        cita_id = await _crear_cita_cercana(s, serv, rec)
        err = await ejecutar(_call("reagendar_cita", cita_id=cita_id, nuevo_inicio=_futuro(hora=11).isoformat()), _ctx(TEL_A), _deps(s))
        assert isinstance(err, ErrorTool) and err.error == "fuera_de_politica" and err.recuperable

        # Reagendar a un cupo ya ocupado → propaga alternativas.
        a = await ejecutar(_call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=14).isoformat(), nombre="A", recurso_id=rec), _ctx(TEL_A), _deps(s))
        await ejecutar(_call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=15).isoformat(), nombre="A", recurso_id=rec), _ctx(TEL_A), _deps(s))
        await s.commit()
        choca = await ejecutar(_call("reagendar_cita", cita_id=a.data["cita_id"], nuevo_inicio=_futuro(hora=15).isoformat()), _ctx(TEL_A), _deps(s))
        assert isinstance(choca, ErrorTool) and choca.error == "cupo_no_disponible"


async def test_reagendar_no_permitido(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        await s.execute(text("UPDATE agenda_config SET permite_reagendar = false WHERE id = 1"))
        await s.commit()
        a = await ejecutar(_call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=10).isoformat(), nombre="A", recurso_id=rec), _ctx(TEL_A), _deps(s))
        await s.commit()
        err = await ejecutar(_call("reagendar_cita", cita_id=a.data["cita_id"], nuevo_inicio=_futuro(hora=11).isoformat()), _ctx(TEL_A), _deps(s))
        assert isinstance(err, ErrorTool) and err.error == "reagendar_no_permitido" and not err.recuperable


async def test_cancelar_fuera_de_politica(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        cita_id = await _crear_cita_cercana(s, serv, rec)
        err = await ejecutar(_call("cancelar_cita", cita_id=cita_id), _ctx(TEL_A), _deps(s))
        assert isinstance(err, ErrorTool) and err.error == "fuera_de_politica" and err.recuperable


async def test_consultar_sin_cupos_y_mis_citas_vacias(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, _ = await _seed(s)
        ayer = (today_co() - timedelta(days=1)).isoformat()  # pasado: anticipación lo deja sin cupos
        r = await ejecutar(_call("consultar_disponibilidad", servicio_id=serv, desde=ayer, hasta=ayer), _ctx(), _deps(s))
        assert isinstance(r, Resultado) and r.data["slots"] == []

        vacio = await ejecutar(_call("mis_citas"), _ctx("3000000000"), _deps(s))
        assert isinstance(vacio, Resultado) and vacio.data["citas"] == []


async def test_agendar_sin_recurso_cupo_invalido_da_alternativas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, _ = await _seed(s)
        # Sin recurso y un inicio fuera de la rejilla (10:07): no hay coincidencia exacta → alternativas.
        err = await ejecutar(
            _call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=10, minuto=7).isoformat(), nombre="A"),
            _ctx(TEL_A), _deps(s),
        )
        assert isinstance(err, ErrorTool) and err.error == "cupo_no_disponible" and err.recuperable


async def test_consultar_recurso_invalido(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, _ = await _seed(s)
        repo = SqlAgendaRepository(s)
        otro = await repo.crear_recurso(RecursoCrear(nombre="Sala 2", tipo="sala"))  # no presta el servicio
        await s.commit()

        inexistente = await ejecutar(_call("consultar_disponibilidad", servicio_id=serv, recurso_id=99999), _ctx(), _deps(s))
        assert isinstance(inexistente, ErrorTool) and inexistente.error == "recurso_no_encontrado"

        no_presta = await ejecutar(_call("consultar_disponibilidad", servicio_id=serv, recurso_id=otro.id), _ctx(), _deps(s))
        assert isinstance(no_presta, ErrorTool) and no_presta.error == "validacion"


async def test_ejecutar_herramienta_desconocida(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed(s)
        err = await ejecutar(_call("no_existe"), _ctx(), _deps(s))
        assert isinstance(err, ErrorTool) and err.error == "error_interno"


# --- falla cerrada sin teléfono ---------------------------------------------
async def test_falla_cerrada_si_falta_telefono_en_contexto(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed(s)
        for tc in (
            _call("mis_citas"),
            _call("agendar_cita", servicio_id=serv, inicio=_futuro(hora=10).isoformat(), nombre="A", recurso_id=rec),
            _call("cancelar_cita", cita_id=1),
        ):
            err = await ejecutar(tc, _ctx(telefono=None), _deps(s))
            assert isinstance(err, ErrorTool)
            assert err.error == "contexto_invalido"
