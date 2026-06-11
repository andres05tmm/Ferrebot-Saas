"""Pack postventa (plan §2.6) + analítica del dueño — contra base efímera real.

Cubre: el barrido elige citas cumplidas / pedidos entregados tras la espera (y respeta la ventana
máxima), dedup (no repite), envío fallido no sella, disparadores configurables, la herramienta
`calificar_atencion` (umbral de reseña, escalamiento en calificación baja, gating, teléfono del
contexto) y el reporte agregado del agente por bloques de capacidad.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto, ErrorTool, Resultado
from ai.postventa_tools import PostventaDeps, ejecutar, exponer_catalogo
from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.config.timezone import COLOMBIA_TZ, today_co
from core.db.session import get_tenant_db
from core.llm.base import ToolCall
from modules.postventa.repository import SqlPostventaRepository
from modules.postventa.service import PostventaService, SeguimientoPendiente
from modules.reportes_agente.router import router as reportes_router

TEL = "3001112233"


def _ahora() -> datetime:
    return datetime.combine(today_co(), time(12, 0), tzinfo=COLOMBIA_TZ)


def _fake_enviar(registro: list, *, ok: bool = True):
    async def enviar(p: SeguimientoPendiente) -> bool:
        registro.append((p.origen, p.origen_id))
        return ok
    return enviar


async def _seed_cita_cumplida(s: AsyncSession, *, fin: datetime, tel: str = TEL) -> int:
    """Cita cumplida con servicio/recurso mínimos (insert directo: el motor no importa aquí)."""
    servicio = (
        await s.execute(
            text("INSERT INTO servicios (nombre, duracion_min) VALUES ('Corte', 30) RETURNING id")
        )
    ).scalar_one()
    recurso = (
        await s.execute(
            text("INSERT INTO recursos (nombre, tipo) VALUES ('Silla', 'sala') RETURNING id")
        )
    ).scalar_one()
    cita = (
        await s.execute(
            text(
                "INSERT INTO citas (servicio_id, recurso_id, cliente_nombre, cliente_telefono, "
                "inicio, fin, estado, origen) "
                "VALUES (:s, :r, 'Ana', :t, :i, :f, 'cumplida', 'whatsapp') RETURNING id"
            ),
            {"s": servicio, "r": recurso, "t": tel, "i": fin - timedelta(minutes=30), "f": fin},
        )
    ).scalar_one()
    await s.commit()
    return cita


async def _seed_pedido_entregado(s: AsyncSession, *, cuando: datetime, tel: str = TEL) -> int:
    pedido = (
        await s.execute(
            text(
                "INSERT INTO pedidos (cliente_telefono, estado, subtotal, total, actualizado_en) "
                "VALUES (:t, 'entregado', 30000, 33000, :c) RETURNING id"
            ),
            {"t": tel, "c": cuando},
        )
    ).scalar_one()
    await s.commit()
    return pedido


# --- barrido del cron -----------------------------------------------------------
async def test_barrido_elige_tras_la_espera_y_dedup(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ahora = _ahora()
        lista = await _seed_cita_cumplida(s, fin=ahora - timedelta(hours=5))      # ya pasó la espera (3h)
        await _seed_cita_cumplida(s, fin=ahora - timedelta(hours=1), tel="3002")  # aún no (espera 3h)
        await _seed_cita_cumplida(s, fin=ahora - timedelta(hours=72), tel="3003") # fuera de ventana (48h)
        pedido = await _seed_pedido_entregado(s, cuando=ahora - timedelta(hours=4))
        svc = PostventaService(SqlPostventaRepository(s))

        registro: list = []
        r1 = await svc.procesar_seguimientos(ahora=ahora, enviar=_fake_enviar(registro))
        await s.commit()
        registro2: list = []
        r2 = await svc.procesar_seguimientos(ahora=ahora, enviar=_fake_enviar(registro2))
        await s.commit()

    assert sorted(registro) == sorted([("cita", lista), ("pedido", pedido)])
    assert r1.enviados == 2
    assert registro2 == [] and r2.enviados == 0           # dedup: jamás se repite


async def test_envio_fallido_no_sella_y_config_gatea(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ahora = _ahora()
        await _seed_cita_cumplida(s, fin=ahora - timedelta(hours=5))
        repo = SqlPostventaRepository(s)
        svc = PostventaService(repo)

        r = await svc.procesar_seguimientos(ahora=ahora, enviar=_fake_enviar([], ok=False))
        await s.commit()
        assert r.enviados == 0
        retry: list = []
        await svc.procesar_seguimientos(ahora=ahora, enviar=_fake_enviar(retry))
        assert len(retry) == 1                            # sin dedup sellado → se reintenta

        config = await repo.obtener_config()
        config.seguir_citas = False
        await s.commit()
        nada: list = []
        await svc.procesar_seguimientos(ahora=ahora, enviar=_fake_enviar(nada))
        assert nada == []                                 # disparador apagado


# --- herramienta calificar_atencion ----------------------------------------------
def _ctx(telefono: str | None = TEL, *, con_flag: bool = True) -> Contexto:
    capacidades = frozenset({"pack_postventa"}) if con_flag else frozenset()
    return Contexto(
        tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
        capacidades=capacidades, cliente_telefono=telefono,
    )


async def test_calificar_umbral_resena_y_baja(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlPostventaRepository(s)
        config = await repo.obtener_config()
        config.google_maps_url = "https://maps.app.goo.gl/negocio"
        await s.commit()
        deps = PostventaDeps(postventa=PostventaService(repo))

        alta = await ejecutar(
            ToolCall(id="t", name="calificar_atencion", arguments={"calificacion": 5}), _ctx(), deps
        )
        assert isinstance(alta, Resultado)
        assert alta.data["link_resena"] == "https://maps.app.goo.gl/negocio"

        baja = await ejecutar(
            ToolCall(id="t", name="calificar_atencion",
                     arguments={"calificacion": 1, "comentario": "Llegó frío"}),
            _ctx(), deps,
        )
        await s.commit()
        assert isinstance(baja, Resultado) and baja.data["link_resena"] is None
        assert "escalar_humano" in baja.resumen           # guía la disculpa + handoff

        respuestas = await deps.postventa.listar_respuestas()
        assert [r.calificacion for r in respuestas] == [1, 5]
        assert respuestas[0].comentario == "Llegó frío"
        assert (await deps.postventa.satisfaccion()) == {"promedio": 3.0, "respuestas": 2}


async def test_gating_y_telefono_del_contexto(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        deps = PostventaDeps(postventa=PostventaService(SqlPostventaRepository(s)))
        assert exponer_catalogo(_ctx(con_flag=False)) == []
        assert [t.name for t in exponer_catalogo(_ctx())] == ["calificar_atencion"]

        sin_tel = await ejecutar(
            ToolCall(id="t", name="calificar_atencion", arguments={"calificacion": 4}),
            _ctx(telefono=None), deps,
        )
        assert isinstance(sin_tel, ErrorTool) and sin_tel.error == "contexto_invalido"

        invalido = await ejecutar(
            ToolCall(id="t", name="calificar_atencion", arguments={"calificacion": 9}), _ctx(), deps
        )
        assert isinstance(invalido, ErrorTool) and invalido.error == "validacion"


# --- analítica del dueño -----------------------------------------------------------
async def test_reporte_agente_bloques_por_capacidad(tenant):
    capacidades = frozenset({"canal_whatsapp", "pack_agenda", "pack_postventa"})
    app = FastAPI()
    app.include_router(reportes_router, prefix="/api/v1")

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="pr", rol="admin")
    app.dependency_overrides[get_tenant_db] = _db
    app.dependency_overrides[get_capacidades] = lambda: capacidades

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ahora = _ahora()
        await _seed_cita_cumplida(s, fin=ahora - timedelta(hours=5))
        await s.execute(
            text(
                "INSERT INTO conversaciones (cliente_telefono, estado, escalada_en) "
                "VALUES ('3001', 'humano', :e), ('3002', 'bot', NULL)"
            ),
            {"e": ahora - timedelta(hours=2)},
        )
        await s.execute(
            text("INSERT INTO encuestas_respuestas (telefono, calificacion) VALUES ('3001', 5)")
        )
        await s.commit()

    cliente = httpx.AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t"
    )
    async with cliente as c:
        r = await c.get("/api/v1/agente/reporte")
        assert r.status_code == 200
        cuerpo = r.json()

    assert cuerpo["conversaciones"]["nuevas"] == 2
    assert cuerpo["conversaciones"]["escaladas_a_humano"] == 1
    assert cuerpo["conversaciones"]["pct_resueltas_sin_humano"] == 50
    assert cuerpo["citas"]["total"] == 1 and cuerpo["citas"]["por_estado"]["cumplida"] == 1
    assert cuerpo["satisfaccion"] == {"promedio": 5.0, "respuestas": 1}
    # Bloques de packs NO activos no aparecen (pedidos/cotizaciones/cobranza).
    assert "pedidos" not in cuerpo and "cobranza" not in cuerpo
