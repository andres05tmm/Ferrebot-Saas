"""Modo demo (Fase 3c): el seeder de showcase por vertical contra base efímera real.

Cubre: que `seed_showcase` siembra catálogo + citas + conversaciones con hilo (conversacion_mensajes) +
encuestas en la BD del tenant; que es idempotente (re-sembrar no duplica); que el hilo se lee ordenado
(repo del inbox); que los datos producen KPIs sensatos (citas de hoy, % resueltas, satisfacción); y la
guarda que rehúsa sembrar sobre Punto Rojo.
"""
import dataclasses
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import today_co
from modules.conversaciones.repository import SqlConversacionRepository
from tools.seed_demo import VERTICALES, seed_showcase

V = VERTICALES["barberia-demo"]   # tiene pack_postventa → siembra encuestas


async def _count(s: AsyncSession, tabla: str, where: str = "") -> int:
    return (await s.execute(text(f"SELECT count(*) FROM {tabla} {where}"))).scalar_one()


async def test_siembra_catalogo_citas_conversaciones_y_encuestas(tenant):
    resumen = seed_showcase(tenant.url, V)

    async with AsyncSession(tenant.engine) as s:
        # Catálogo.
        assert await _count(s, "servicios") == len(V.servicios)
        assert await _count(s, "recursos") == len(V.recursos)
        assert await _count(s, "agenda_config") == 1
        # Citas: las de hoy + las pasadas cumplidas.
        assert await _count(s, "citas") == len(V.citas_hoy) + V.n_citas_pasadas
        assert resumen["citas"] == len(V.citas_hoy) + V.n_citas_pasadas
        # Conversaciones + hilo.
        assert await _count(s, "conversaciones") == len(V.hilos)
        assert await _count(s, "conversacion_mensajes") == sum(len(h.mensajes) for h in V.hilos)
        # Encuestas (tiene postventa).
        assert await _count(s, "encuestas_respuestas") == V.n_encuestas


async def test_idempotente_no_duplica(tenant):
    seed_showcase(tenant.url, V)
    seed_showcase(tenant.url, V)   # segunda corrida
    async with AsyncSession(tenant.engine) as s:
        assert await _count(s, "servicios") == len(V.servicios)        # catálogo: get-or-create
        assert await _count(s, "citas") == len(V.citas_hoy) + V.n_citas_pasadas
        assert await _count(s, "conversaciones") == len(V.hilos)       # showcase: borra + re-siembra
        assert await _count(s, "conversacion_mensajes") == sum(len(h.mensajes) for h in V.hilos)


async def test_hilo_se_lee_ordenado_en_el_inbox(tenant):
    seed_showcase(tenant.url, V)
    primer = V.hilos[0]
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlConversacionRepository(s)
        inbox = await repo.listar_inbox()
        telefonos = {f.conversacion.cliente_telefono for f in inbox}
        assert {h.telefono for h in V.hilos} <= telefonos          # todas las conversaciones del demo
        hilo = await repo.listar_mensajes(primer.telefono)
        assert [m.texto for m in hilo] == [m[1] for m in primer.mensajes]   # mismo orden cronológico


async def test_kpis_sensatos(tenant):
    seed_showcase(tenant.url, V)
    async with AsyncSession(tenant.engine) as s:
        # Citas de HOY en hora Colombia (rango tz-aware, como el reporte; `::date` casteaba en UTC).
        hoy = today_co()
        desde, hasta = f"{hoy}T00:00:00-05:00", f"{hoy + timedelta(days=1)}T00:00:00-05:00"
        citas_hoy = await _count(s, "citas", f"WHERE inicio >= '{desde}' AND inicio < '{hasta}'")
        assert citas_hoy == len(V.citas_hoy)
        # Handoff: hay al menos una escalada a humano y la mayoría las resolvió el bot.
        humano = await _count(s, "conversaciones", "WHERE estado = 'humano'")
        assert humano >= 1 and humano < len(V.hilos)
        # Satisfacción: promedio razonable (reparto centrado en 4–5).
        prom = (await s.execute(text("SELECT AVG(calificacion) FROM encuestas_respuestas"))).scalar_one()
        assert prom is not None and 4.0 <= float(prom) <= 5.0


async def test_rehusa_sembrar_punto_rojo(tenant):
    protegido = dataclasses.replace(V, slug="puntorojo")
    with pytest.raises(ValueError, match="protegido"):
        seed_showcase(tenant.url, protegido)
    # No escribió nada (la guarda corta antes de tocar la BD).
    async with AsyncSession(tenant.engine) as s:
        assert await _count(s, "citas") == 0
        assert await _count(s, "conversaciones") == 0
