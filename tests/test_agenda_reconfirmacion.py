"""Reconfirmación de citas (anti-no-show) — el job determinista sobre la base efímera real.

Cubre: selección del recordatorio por la ventana de `recordatorios_horas`; dedup (no reenvía);
flip a `en_riesgo` al llegar `corte_riesgo_horas` sin respuesta; que `reconfirmada`/`pendiente` no se
toquen; y la regla clave: `en_riesgo` (no-respuesta) NUNCA libera el cupo, solo la cancelación sí.

El envío real por Kapso se inyecta como callback (`enviar`) y aquí se falsea: el job no manda WhatsApp,
solo decide a qué citas tocar.
"""
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.agenda.models import Cita
from modules.agenda.repository import SqlAgendaRepository
from modules.agenda.schemas import (
    AgendaConfigCrear,
    CitaCrear,
    RecursoCrear,
    ServicioCrear,
)
from modules.agenda.service import AgendaService


def _fake_enviar(registro: list[int], *, ok: bool = True):
    """Callback de envío falso: registra el id de cada cita y reporta éxito (`ok`)."""
    async def enviar(cita: Cita) -> bool:
        registro.append(cita.id)
        return ok
    return enviar


async def _seed_base(
    s: AsyncSession, *, recordatorios: list[int] | None = None, corte: int = 2
) -> tuple[int, int]:
    """Servicio + recurso + config de reconfirmación. Devuelve (servicio_id, recurso_id)."""
    repo = SqlAgendaRepository(s)
    serv = await repo.crear_servicio(ServicioCrear(nombre="Limpieza", duracion_min=30))
    rec = await repo.crear_recurso(RecursoCrear(nombre="Dra. Pérez", tipo="profesional"))
    await repo.asignar_servicio(recurso_id=rec.id, servicio_id=serv.id)
    await repo.guardar_config(
        AgendaConfigCrear(
            recordatorios_horas=recordatorios if recordatorios is not None else [24, 2],
            corte_riesgo_horas=corte, anticipacion_minima_min=0, ventana_maxima_dias=60,
        )
    )
    await s.commit()
    return serv.id, rec.id


async def _crear_cita(
    s: AsyncSession, serv: int, rec: int, *, inicio: datetime,
    estado: str = "confirmada", tel: str = "3001234567", nombre: str = "Ana",
) -> Cita:
    """Inserta una cita con inicio controlado (bypassa el motor: la reconfirmación no toca el cupo)."""
    repo = SqlAgendaRepository(s)
    fin = inicio + timedelta(minutes=30)
    cita = await repo.crear_cita(
        CitaCrear(
            servicio_id=serv, recurso_id=rec, cliente_nombre=nombre, cliente_telefono=tel,
            inicio=inicio, fin=fin, origen="whatsapp",
        ),
        estado=estado, fin=fin,
    )
    await s.commit()
    return cita


async def _releer(tenant, cita_id: int) -> Cita:
    async with AsyncSession(tenant.engine) as s2:
        return await s2.get(Cita, cita_id)


# --- recordatorio: selección por ventana + envío ----------------------------
async def test_recordatorio_solo_dentro_de_la_ventana(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed_base(s, recordatorios=[24, 2])
        ahora = now_co()
        dentro = await _crear_cita(s, serv, rec, inicio=ahora + timedelta(hours=10))
        fuera = await _crear_cita(s, serv, rec, inicio=ahora + timedelta(hours=30), tel="3009990000")

        enviados: list[int] = []
        svc = AgendaService(SqlAgendaRepository(s))
        resumen = await svc.procesar_reconfirmaciones(ahora=ahora, enviar=_fake_enviar(enviados))
        await s.commit()

    assert enviados == [dentro.id]              # solo la que entra en la ventana de 24h
    assert resumen.recordatorios == 1
    assert (await _releer(tenant, dentro.id)).recordatorio_enviado_en is not None  # dedup sellado
    assert (await _releer(tenant, fuera.id)).recordatorio_enviado_en is None


async def test_recordatorio_dedup_no_reenvia(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed_base(s, recordatorios=[24])
        ahora = now_co()
        await _crear_cita(s, serv, rec, inicio=ahora + timedelta(hours=10))
        svc = AgendaService(SqlAgendaRepository(s))

        primera: list[int] = []
        await svc.procesar_reconfirmaciones(ahora=ahora, enviar=_fake_enviar(primera))
        await s.commit()
        segunda: list[int] = []
        r2 = await svc.procesar_reconfirmaciones(ahora=ahora, enviar=_fake_enviar(segunda))
        await s.commit()

    assert len(primera) == 1
    assert segunda == [] and r2.recordatorios == 0   # ya tiene recordatorio_enviado_en


async def test_envio_fallido_no_sella_dedup(tenant):
    """Si `enviar` devuelve False (Google/Kapso caído), no se sella el dedup: se reintenta luego."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed_base(s, recordatorios=[24])
        ahora = now_co()
        cita = await _crear_cita(s, serv, rec, inicio=ahora + timedelta(hours=10))
        svc = AgendaService(SqlAgendaRepository(s))
        r = await svc.procesar_reconfirmaciones(ahora=ahora, enviar=_fake_enviar([], ok=False))
        await s.commit()

    assert r.recordatorios == 0
    assert (await _releer(tenant, cita.id)).recordatorio_enviado_en is None


# --- corte de riesgo --------------------------------------------------------
async def test_flip_en_riesgo_en_el_corte_sin_liberar_cupo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed_base(s, corte=2)
        ahora = now_co()
        riesgo = await _crear_cita(s, serv, rec, inicio=ahora + timedelta(hours=1))   # dentro del corte
        lejos = await _crear_cita(s, serv, rec, inicio=ahora + timedelta(hours=5), tel="3009990000")
        svc = AgendaService(SqlAgendaRepository(s))
        resumen = await svc.procesar_reconfirmaciones(ahora=ahora, enviar=_fake_enviar([]))
        await s.commit()

    assert resumen.en_riesgo == 1
    en_riesgo = await _releer(tenant, riesgo.id)
    assert en_riesgo.confirmacion == "en_riesgo"
    assert en_riesgo.estado == "confirmada"        # NUNCA libera el cupo
    assert (await _releer(tenant, lejos.id)).confirmacion == "esperando"   # fuera del corte


async def test_reconfirmada_no_recibe_recordatorio_ni_riesgo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed_base(s, corte=2)
        ahora = now_co()
        cita = await _crear_cita(s, serv, rec, inicio=ahora + timedelta(hours=1))
        repo = SqlAgendaRepository(s)
        await repo.marcar_confirmacion(cita, "reconfirmada")
        await s.commit()

        enviados: list[int] = []
        svc = AgendaService(repo)
        resumen = await svc.procesar_reconfirmaciones(ahora=ahora, enviar=_fake_enviar(enviados))
        await s.commit()

    assert enviados == [] and resumen.recordatorios == 0 and resumen.en_riesgo == 0
    assert (await _releer(tenant, cita.id)).confirmacion == "reconfirmada"


async def test_pendiente_no_se_reconfirma(tenant):
    """La reconfirmación es del cliente sobre una cita en firme: las `pendiente` (esperan al negocio) no."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed_base(s, corte=2)
        ahora = now_co()
        pend = await _crear_cita(s, serv, rec, inicio=ahora + timedelta(hours=1), estado="pendiente")
        enviados: list[int] = []
        svc = AgendaService(SqlAgendaRepository(s))
        resumen = await svc.procesar_reconfirmaciones(ahora=ahora, enviar=_fake_enviar(enviados))
        await s.commit()

    assert enviados == [] and resumen.recordatorios == 0 and resumen.en_riesgo == 0
    assert (await _releer(tenant, pend.id)).confirmacion == "esperando"


# --- liberación del cupo: solo la cancelación ------------------------------
async def test_en_riesgo_no_libera_cupo_cancelar_si(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        serv, rec = await _seed_base(s, corte=2)
        ahora = now_co()
        inicio = ahora + timedelta(hours=1)
        cita = await _crear_cita(s, serv, rec, inicio=inicio)
        repo = SqlAgendaRepository(s)
        ventana = (inicio - timedelta(hours=1), inicio + timedelta(hours=1))

        await repo.marcar_confirmacion(cita, "en_riesgo")
        await s.commit()
        ocupado = await repo.ocupaciones_de_recurso(recurso_id=rec, inicio=ventana[0], fin=ventana[1])
        assert len(ocupado) == 1                  # no-respuesta (en_riesgo) NO libera el cupo

        await repo.cambiar_estado_cita(cita, "cancelada")
        await s.commit()
        libre = await repo.ocupaciones_de_recurso(recurso_id=rec, inicio=ventana[0], fin=ventana[1])
        assert libre == []                        # la cancelación SÍ libera
