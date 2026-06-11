"""Pack reservas (plan §2.7) — la variante noches del motor de agenda, contra base efímera real.

Cubre: habitaciones libres (resta de citas/bloqueos del rango check-in→check-out), tarifa por
noche desde el servicio, reservar crea la cita con las horas de check-in/checkout de la config,
anti-doble-reserva (revalida bajo lock), anticipo (porcentaje/fijo → estado pendiente + cobro con
link cuando hay pagos), idempotencia y los guardarraíles de las herramientas.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.reservas_tools import ReservasDeps, ejecutar, exponer_catalogo
from core.config.timezone import today_co
from core.llm.base import ToolCall
from modules.agenda.errors import CupoNoDisponible
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import AgendaConfigCrear, RecursoCrear, ServicioCrear
from modules.pagos.repository import SqlPagosRepository
from modules.pagos.service import PagosService
from modules.reservas.service import ReservasService
from tests.test_pagos import FakePsp

TEL = "3001112233"


def _checkin(dias: int = 7) -> date:
    return today_co() + timedelta(days=dias)


async def _seed_hotel(s: AsyncSession, *, habitaciones: int = 2, precio: str = "180000",
                      **config) -> list[int]:
    """Servicio 'Noche' + N habitaciones que lo prestan + agenda_config. Devuelve recurso_ids."""
    repo = SqlAgendaRepository(s)
    servicio = await repo.crear_servicio(
        ServicioCrear(nombre="Noche estándar", duracion_min=60, precio=precio)
    )
    ids: list[int] = []
    for n in range(1, habitaciones + 1):
        recurso = await repo.crear_recurso(RecursoCrear(nombre=f"Habitación {n}", tipo="habitacion"))
        await repo.asignar_servicio(recurso_id=recurso.id, servicio_id=servicio.id)
        ids.append(recurso.id)
    await repo.guardar_config(AgendaConfigCrear(**config))
    await s.commit()
    return ids


def _ctx(telefono: str | None = TEL, *, capacidades=frozenset({"pack_reservas"})) -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
        capacidades=capacidades, cliente_telefono=telefono,
    )


def _call(herramienta: str, **arguments) -> ToolCall:
    return ToolCall(id="t", name=herramienta, arguments=arguments)


# --- disponibilidad + reserva -------------------------------------------------
async def test_habitaciones_libres_y_reserva_ocupa_el_rango(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed_hotel(s)
        svc = ReservasService(SqlAgendaRepository(s))

        libres = await svc.habitaciones_libres(_checkin(), 3)
        assert {h.recurso_id for h in libres} == set(ids)
        assert libres[0].precio_noche == Decimal("180000.00")
        assert libres[0].total == Decimal("540000.00")            # 3 noches

        res = await svc.reservar(
            recurso_id=ids[0], checkin=_checkin(), noches=3,
            cliente_nombre="Ana", cliente_telefono=TEL,
        )
        await s.commit()
        cita = res.cita
        assert cita.estado == "confirmada" and res.anticipo is None
        assert cita.inicio.astimezone().hour in (15, 20)   # 15:00 CO (20 UTC) — check-in default
        assert (cita.fin - cita.inicio) >= timedelta(days=2, hours=20)   # ~3 noches

        # La habitación reservada desaparece; la otra sigue (incluye solape parcial).
        libres2 = await svc.habitaciones_libres(_checkin() + timedelta(days=1), 1)
        assert {h.recurso_id for h in libres2} == {ids[1]}


async def test_doble_reserva_choca_bajo_lock(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed_hotel(s, habitaciones=1)
        svc = ReservasService(SqlAgendaRepository(s))
        await svc.reservar(
            recurso_id=ids[0], checkin=_checkin(), noches=2,
            cliente_nombre="Ana", cliente_telefono=TEL,
        )
        with pytest.raises(CupoNoDisponible):
            await svc.reservar(
                recurso_id=ids[0], checkin=_checkin(1 + 7), noches=2,   # solapa la 2ª noche
                cliente_nombre="Bruno", cliente_telefono="3009998877",
            )


async def test_idempotencia_por_key(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed_hotel(s, habitaciones=1)
        svc = ReservasService(SqlAgendaRepository(s))
        r1 = await svc.reservar(
            recurso_id=ids[0], checkin=_checkin(), noches=2,
            cliente_nombre="Ana", cliente_telefono=TEL, idempotency_key="res-1",
        )
        await s.commit()
        r2 = await svc.reservar(
            recurso_id=ids[0], checkin=_checkin(), noches=2,
            cliente_nombre="Ana", cliente_telefono=TEL, idempotency_key="res-1",
        )
    assert r2.replay and r2.cita.id == r1.cita.id


# --- anticipo (requiere_anticipo + frente de pagos) -----------------------------
async def test_anticipo_porcentaje_deja_pendiente_y_crea_cobro_con_link(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed_hotel(
            s, habitaciones=1,
            requiere_anticipo=True, anticipo_tipo="porcentaje", anticipo_valor=Decimal("50"),
        )
        deps = ReservasDeps(
            reservas=ReservasService(SqlAgendaRepository(s)),
            pagos=PagosService(SqlPagosRepository(s), psp=FakePsp()),
        )
        r = await ejecutar(
            _call("reservar_habitacion", recurso_id=ids[0],
                  checkin=str(_checkin()), noches=2, nombre="Ana"),
            _ctx(capacidades=frozenset({"pack_reservas", "pagos_online"})), deps,
        )
        await s.commit()

    assert isinstance(r, Resultado)
    assert r.data["estado"] == "pendiente"                       # hasta que pague
    assert r.data["anticipo"] == "180000"                        # 50% de 360.000
    assert r.data["cobro"]["url"].startswith("https://checkout.bold.co/")
    assert "anticipo" in r.resumen and "Puede pagarlo aquí" in r.resumen


async def test_anticipo_fijo_sin_pagos_informa_sin_link(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed_hotel(
            s, habitaciones=1,
            requiere_anticipo=True, anticipo_tipo="fijo", anticipo_valor=Decimal("100000"),
        )
        deps = ReservasDeps(reservas=ReservasService(SqlAgendaRepository(s)))   # sin pagos
        r = await ejecutar(
            _call("reservar_habitacion", recurso_id=ids[0],
                  checkin=str(_checkin()), noches=1, nombre="Ana"),
            _ctx(), deps,
        )
        await s.commit()

    assert isinstance(r, Resultado) and Decimal(r.data["anticipo"]) == Decimal("100000")
    assert "el negocio le indicará" in r.resumen and "cobro" not in r.data


# --- herramientas / guardarraíles ----------------------------------------------
async def test_consultar_noches_y_cupo_perdido(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed_hotel(s, habitaciones=1)
        deps = ReservasDeps(reservas=ReservasService(SqlAgendaRepository(s)))

        libres = await ejecutar(
            _call("consultar_noches", checkin=str(_checkin()), noches=2), _ctx(), deps
        )
        assert isinstance(libres, Resultado) and "Habitación 1" in libres.resumen

        await ejecutar(
            _call("reservar_habitacion", recurso_id=ids[0],
                  checkin=str(_checkin()), noches=2, nombre="Ana"),
            _ctx(), deps,
        )
        perdido = await ejecutar(
            _call("reservar_habitacion", recurso_id=ids[0],
                  checkin=str(_checkin()), noches=2, nombre="Bruno"),
            _ctx("3009998877"), deps,
        )
        assert isinstance(perdido, ErrorTool) and perdido.error == "cupo_no_disponible"

        vacio = await ejecutar(
            _call("consultar_noches", checkin=str(_checkin()), noches=1), _ctx(), deps
        )
        assert isinstance(vacio, Resultado) and vacio.data["habitaciones"] == []


def test_catalogo_gateado_por_flag():
    assert exponer_catalogo(_ctx(capacidades=frozenset())) == []
    nombres = [spec.name for spec in exponer_catalogo(_ctx())]
    assert nombres == ["consultar_noches", "reservar_habitacion"]


async def test_sin_telefono_falla_cerrado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        deps = ReservasDeps(reservas=ReservasService(SqlAgendaRepository(s)))
        r = await ejecutar(
            _call("reservar_habitacion", recurso_id=1, checkin=str(_checkin()), noches=1, nombre="A"),
            _ctx(telefono=None), deps,
        )
        assert isinstance(r, ErrorTool) and r.error == "contexto_invalido"
