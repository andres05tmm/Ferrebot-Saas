"""Router del pack Agenda (dashboard) por HTTP contra base efímera real.

Patrón test_compras: app mínima + ASGITransport + overrides de auth, sesión del tenant (que hace
commit, para persistir y entregar el pg_notify) y capacidades. Cubre: gating por flag (404), RBAC
(admin vs staff), CRUD de catálogo/config, filtros de citas, acciones del negocio
(confirmar/cancelar/reagendar/alta manual) y la emisión del evento SSE.
"""
import asyncio
import json
from datetime import datetime, time, timedelta

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import COLOMBIA_TZ, today_co
from core.db.session import get_tenant_db
from core.events.hub import event_hub
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.router import router as agenda_router
from modules.agenda.schemas import (
    AgendaConfigCrear,
    DisponibilidadCrear,
    RecursoCrear,
    ServicioCrear,
)

PACK = frozenset({"pack_agenda"})


def _futuro(hora: int = 10, minuto: int = 0, dias: int = 3) -> datetime:
    return datetime.combine(today_co() + timedelta(days=dias), time(hora, minuto), tzinfo=COLOMBIA_TZ)


def _app(tenant, *, rol: str = "admin", capacidades=PACK) -> FastAPI:
    app = FastAPI()
    app.include_router(agenda_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol=rol)
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: capacidades
    return app


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def _seed_catalogo(tenant, *, modo: str = "auto") -> tuple[int, int]:
    """Crea servicio + recurso + N:N + disponibilidad (semana 08–18) + config. Devuelve (serv, rec)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlAgendaRepository(s)
        serv = await repo.crear_servicio(ServicioCrear(nombre="Limpieza", duracion_min=30, precio="80000"))
        rec = await repo.crear_recurso(RecursoCrear(nombre="Dra. Pérez", tipo="profesional"))
        await repo.asignar_servicio(recurso_id=rec.id, servicio_id=serv.id)
        for dia in range(7):
            await repo.crear_disponibilidad(
                DisponibilidadCrear(recurso_id=rec.id, dia_semana=dia, hora_inicio=time(8), hora_fin=time(18))
            )
        await repo.guardar_config(
            AgendaConfigCrear(modo_confirmacion=modo, anticipacion_minima_min=0,
                              ventana_maxima_dias=60, intervalo_slots_min=30)
        )
        await s.commit()
        return serv.id, rec.id


# --- gating por flag --------------------------------------------------------
async def test_sin_flag_pack_agenda_da_404(tenant):
    app = _app(tenant, rol="admin", capacidades=frozenset())  # sin la capacidad
    async with _cliente(app) as c:
        r = await c.get("/api/v1/agenda/servicios")
    assert r.status_code == 404


# --- RBAC -------------------------------------------------------------------
async def test_rbac_staff_no_crea_catalogo_admin_si(tenant):
    body = {"nombre": "Corte", "duracion_min": 20}
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        r = await c.post("/api/v1/agenda/servicios", json=body)
    assert r.status_code == 403  # staff no gestiona catálogo

    async with _cliente(_app(tenant, rol="admin")) as c:
        r = await c.post("/api/v1/agenda/servicios", json=body)
    assert r.status_code == 201 and r.json()["nombre"] == "Corte"


# --- CRUD de catálogo -------------------------------------------------------
async def test_crud_servicios_y_recursos(tenant):
    async with _cliente(_app(tenant, rol="admin")) as c:
        serv = (await c.post("/api/v1/agenda/servicios", json={"nombre": "Limpieza", "duracion_min": 30})).json()
        rec = (await c.post("/api/v1/agenda/recursos", json={"nombre": "Silla 1", "tipo": "equipo"})).json()

        # update + get
        up = await c.put(f"/api/v1/agenda/servicios/{serv['id']}", json={"nombre": "Limpieza profunda", "duracion_min": 45})
        assert up.status_code == 200 and up.json()["duracion_min"] == 45

        # N:N asignar + listar recursos del servicio
        asg = await c.post("/api/v1/agenda/recurso-servicio", json={"recurso_id": rec["id"], "servicio_id": serv["id"]})
        assert asg.status_code == 204
        recs = await c.get(f"/api/v1/agenda/servicios/{serv['id']}/recursos")
        assert [x["id"] for x in recs.json()] == [rec["id"]]

        # desactivar (soft) → no aparece en activos, sí con incluir_inactivos
        de = await c.delete(f"/api/v1/agenda/servicios/{serv['id']}")
        assert de.status_code == 200 and de.json()["activo"] is False
        assert (await c.get("/api/v1/agenda/servicios")).json() == []
        assert len((await c.get("/api/v1/agenda/servicios?incluir_inactivos=true")).json()) == 1

        # servicio inexistente → 404
        assert (await c.get("/api/v1/agenda/servicios/99999")).status_code == 404


async def test_disponibilidad_y_bloqueos(tenant):
    async with _cliente(_app(tenant, rol="admin")) as c:
        rec = (await c.post("/api/v1/agenda/recursos", json={"nombre": "Dr. A", "tipo": "profesional"})).json()
        disp = await c.post("/api/v1/agenda/disponibilidad",
                            json={"recurso_id": rec["id"], "dia_semana": 0, "hora_inicio": "08:00", "hora_fin": "12:00"})
        assert disp.status_code == 201
        lista = await c.get(f"/api/v1/agenda/recursos/{rec['id']}/disponibilidad")
        assert len(lista.json()) == 1
        assert (await c.delete(f"/api/v1/agenda/disponibilidad/{disp.json()['id']}")).status_code == 204

    # bloqueos son operativos → staff puede
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        bl = await c.post("/api/v1/agenda/bloqueos",
                          json={"inicio": _futuro(9).isoformat(), "fin": _futuro(11).isoformat(), "motivo": "festivo"})
        assert bl.status_code == 201
        assert len((await c.get("/api/v1/agenda/bloqueos")).json()) == 1
        assert (await c.delete(f"/api/v1/agenda/bloqueos/{bl.json()['id']}")).status_code == 204


async def test_recursos_lifecycle_desasignar_y_404s(tenant):
    async with _cliente(_app(tenant, rol="admin")) as c:
        serv = (await c.post("/api/v1/agenda/servicios", json={"nombre": "Masaje", "duracion_min": 60})).json()
        rec = (await c.post("/api/v1/agenda/recursos", json={"nombre": "Sala A", "tipo": "sala"})).json()

        assert (await c.get(f"/api/v1/agenda/recursos/{rec['id']}")).json()["nombre"] == "Sala A"
        up = await c.put(f"/api/v1/agenda/recursos/{rec['id']}", json={"nombre": "Sala B", "tipo": "sala"})
        assert up.status_code == 200 and up.json()["nombre"] == "Sala B"

        await c.post("/api/v1/agenda/recurso-servicio", json={"recurso_id": rec["id"], "servicio_id": serv["id"]})
        des = await c.request("DELETE", f"/api/v1/agenda/recurso-servicio?recurso_id={rec['id']}&servicio_id={serv['id']}")
        assert des.status_code == 204
        assert (await c.get(f"/api/v1/agenda/servicios/{serv['id']}/recursos")).json() == []

        de = await c.delete(f"/api/v1/agenda/recursos/{rec['id']}")
        assert de.status_code == 200 and de.json()["activo"] is False
        assert (await c.get("/api/v1/agenda/recursos/99999")).status_code == 404

        # eliminar disponibilidad/bloqueo inexistentes → 404
        assert (await c.delete("/api/v1/agenda/disponibilidad/99999")).status_code == 404
        assert (await c.delete("/api/v1/agenda/bloqueos/99999")).status_code == 404


# --- agenda_config ----------------------------------------------------------
async def test_config_get_put(tenant):
    async with _cliente(_app(tenant, rol="admin")) as c:
        assert (await c.get("/api/v1/agenda/config")).status_code == 404  # aún sin configurar
        put = await c.put("/api/v1/agenda/config", json={"intervalo_slots_min": 20, "modo_confirmacion": "manual"})
        assert put.status_code == 200 and put.json()["intervalo_slots_min"] == 20
        got = await c.get("/api/v1/agenda/config")
        assert got.json()["modo_confirmacion"] == "manual"

    # staff no puede editar config
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        assert (await c.put("/api/v1/agenda/config", json={"intervalo_slots_min": 15})).status_code == 403


# --- citas: alta manual, filtros, acciones ----------------------------------
async def test_alta_manual_y_filtros(tenant):
    serv, rec = await _seed_catalogo(tenant, modo="auto")
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        # slots del motor
        slots = await c.get(f"/api/v1/agenda/slots?servicio_id={serv}&desde={_futuro().date()}&hasta={_futuro().date()}")
        assert slots.status_code == 200 and slots.json()

        cuerpo = {"servicio_id": serv, "recurso_id": rec, "inicio": _futuro(10).isoformat(),
                  "cliente_nombre": "Ana", "cliente_telefono": "3001"}
        cita = await c.post("/api/v1/agenda/citas", json=cuerpo)
        assert cita.status_code == 201
        assert cita.json()["origen"] == "dashboard" and cita.json()["estado"] == "confirmada"

        # listar con filtros
        dia = _futuro().date()
        todas = await c.get(f"/api/v1/agenda/citas?desde={dia}&hasta={dia}")
        assert len(todas.json()) == 1
        assert (await c.get(f"/api/v1/agenda/citas?desde={dia}&hasta={dia}&estado=cancelada")).json() == []
        assert len((await c.get(f"/api/v1/agenda/citas?desde={dia}&hasta={dia}&recurso_id={rec}")).json()) == 1
        assert (await c.get(f"/api/v1/agenda/citas?desde={dia}&hasta={dia}&recurso_id=99999")).json() == []

        # detalle
        cid = cita.json()["id"]
        assert (await c.get(f"/api/v1/agenda/citas/{cid}")).json()["id"] == cid


async def test_confirmar_cancelar_reagendar(tenant):
    serv, rec = await _seed_catalogo(tenant, modo="manual")  # alta → pendiente
    async with _cliente(_app(tenant, rol="vendedor")) as c:
        def _crear(hora):
            return c.post("/api/v1/agenda/citas", json={
                "servicio_id": serv, "recurso_id": rec, "inicio": _futuro(hora).isoformat(),
                "cliente_nombre": "Ana", "cliente_telefono": "3001"})

        cita = (await _crear(10)).json()
        assert cita["estado"] == "pendiente"

        conf = await c.post(f"/api/v1/agenda/citas/{cita['id']}/confirmar")
        assert conf.status_code == 200 and conf.json()["estado"] == "confirmada"

        # reagendar a un cupo libre
        re = await c.post(f"/api/v1/agenda/citas/{cita['id']}/reagendar", json={"nuevo_inicio": _futuro(11).isoformat()})
        assert re.status_code == 200 and re.json()["inicio"].startswith(_futuro(11).date().isoformat())

        # ocupar 12:00 y reagendar la cita ahí → 409 con alternativas
        await _crear(12)
        choca = await c.post(f"/api/v1/agenda/citas/{cita['id']}/reagendar", json={"nuevo_inicio": _futuro(12).isoformat()})
        assert choca.status_code == 409
        assert "alternativas" in choca.json()["detail"]

        # cancelar
        canc = await c.post(f"/api/v1/agenda/citas/{cita['id']}/cancelar")
        assert canc.status_code == 200 and canc.json()["estado"] == "cancelada"
        # cancelar una cita ya cancelada (terminal) → 409
        assert (await c.post(f"/api/v1/agenda/citas/{cita['id']}/cancelar")).status_code == 409


# --- SSE --------------------------------------------------------------------
async def test_alta_manual_emite_evento_sse(tenant):
    serv, rec = await _seed_catalogo(tenant)
    queue = await event_hub.subscribe(tenant_id=7373, dsn=tenant.url)
    try:
        async with _cliente(_app(tenant, rol="vendedor")) as c:
            r = await c.post("/api/v1/agenda/citas", json={
                "servicio_id": serv, "recurso_id": rec, "inicio": _futuro(10).isoformat(),
                "cliente_nombre": "Ana", "cliente_telefono": "3001"})
        assert r.status_code == 201
        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        evento = json.loads(payload)
        assert evento["event"] == "cita_agendada"
        assert evento["data"]["cita_id"] == r.json()["id"]
    finally:
        await event_hub.unsubscribe(7373, queue)
