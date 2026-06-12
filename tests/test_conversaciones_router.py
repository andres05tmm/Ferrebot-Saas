"""Router del pack de conversación / handoff (dashboard) por HTTP contra base efímera real.

Patrón test_agenda_router: app mínima + ASGITransport + overrides de auth, sesión del tenant (que hace
commit, para persistir y entregar el pg_notify) y capacidades. Cubre: gating por flag (404), RBAC
(staff), listado de escaladas, la acción 'resolver' (estado→bot) y la emisión del evento SSE.
"""
import asyncio
import json

import httpx
from fastapi import FastAPI
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, get_current_user
from core.auth.features import get_capacidades
from core.db.session import get_tenant_db
from core.events.hub import event_hub
from modules.conversaciones.repository import SqlConversacionRepository
from modules.conversaciones.router import get_enviador_wa, router as conversaciones_router

FLAG = frozenset({"canal_whatsapp"})
TEL_A = "573001112233"
TEL_B = "573009998877"


class _EnviadorFake:
    """Enviador saliente de prueba (override de get_enviador_wa): registra envíos, sin red ni Kapso."""

    def __init__(self) -> None:
        self.envios: list[tuple[int, str, str]] = []

    async def enviar(self, tenant_id: int, to: str, texto: str) -> None:
        self.envios.append((tenant_id, to, texto))


def _app(tenant, *, rol: str = "vendedor", capacidades=FLAG, enviador=None) -> FastAPI:
    app = FastAPI()
    app.include_router(conversaciones_router, prefix="/api/v1")

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
    if enviador is not None:
        app.dependency_overrides[get_enviador_wa] = lambda: enviador
    return app


async def _agregar(tenant, telefono: str, direccion: str, autor: str, texto: str) -> None:
    """Asegura la conversación (invariante del runtime) y agrega un mensaje al hilo."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlConversacionRepository(s)
        await repo.asegurar(telefono)
        await repo.agregar_mensaje(telefono, direccion, autor, texto)
        await s.commit()


def _cliente(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t")


async def _escalar(tenant, telefono: str, motivo: str) -> int:
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        conv = await SqlConversacionRepository(s).escalar(telefono, motivo)
        await s.commit()
        return conv.id


# --- gating por flag --------------------------------------------------------
async def test_sin_flag_canal_whatsapp_da_404(tenant):
    app = _app(tenant, capacidades=frozenset())  # sin la capacidad
    async with _cliente(app) as c:
        r = await c.get("/api/v1/conversaciones/escaladas")
    assert r.status_code == 404


# --- listar escaladas -------------------------------------------------------
async def test_listar_escaladas(tenant):
    await _escalar(tenant, TEL_A, "no resuelvo")
    await _escalar(tenant, TEL_B, "queja")
    async with _cliente(_app(tenant)) as c:
        r = await c.get("/api/v1/conversaciones/escaladas")
    assert r.status_code == 200
    telefonos = {x["cliente_telefono"] for x in r.json()}
    assert telefonos == {TEL_A, TEL_B}
    assert all(x["estado"] == "humano" for x in r.json())


# --- resolver ---------------------------------------------------------------
async def test_resolver_devuelve_al_bot(tenant):
    cid = await _escalar(tenant, TEL_A, "x")
    async with _cliente(_app(tenant)) as c:
        r = await c.post(f"/api/v1/conversaciones/{cid}/resolver")
        assert r.status_code == 200
        assert r.json()["estado"] == "bot" and r.json()["resuelta_en"] is not None
        # Ya no aparece en la bandeja.
        assert (await c.get("/api/v1/conversaciones/escaladas")).json() == []


async def test_resolver_inexistente_da_404(tenant):
    async with _cliente(_app(tenant)) as c:
        r = await c.post("/api/v1/conversaciones/99999/resolver")
    assert r.status_code == 404


# --- inbox: listar todas ----------------------------------------------------
async def test_listar_inbox_con_ultimo_mensaje_y_estado(tenant):
    cid = await _escalar(tenant, TEL_A, "queja")          # A: humano
    await _agregar(tenant, TEL_A, "entrante", "cliente", "quiero un asesor")
    await _agregar(tenant, TEL_B, "entrante", "cliente", "hola bot")   # B: solo mensajes (bot)
    await _agregar(tenant, TEL_B, "saliente", "bot", "¡hola! ¿en qué te ayudo?")
    async with _cliente(_app(tenant)) as c:
        r = await c.get("/api/v1/conversaciones")
    assert r.status_code == 200
    por_tel = {x["cliente_telefono"]: x for x in r.json()}
    assert set(por_tel) == {TEL_A, TEL_B}
    assert por_tel[TEL_A]["estado"] == "humano" and por_tel[TEL_A]["id"] == cid
    assert por_tel[TEL_A]["ultimo_texto"] == "quiero un asesor"
    assert por_tel[TEL_B]["estado"] == "bot"
    assert por_tel[TEL_B]["ultimo_texto"] == "¡hola! ¿en qué te ayudo?"
    assert por_tel[TEL_B]["ultimo_autor"] == "bot"


# --- hilo de mensajes -------------------------------------------------------
async def test_listar_mensajes_hilo_ordenado(tenant):
    cid = await _escalar(tenant, TEL_A, "x")
    await _agregar(tenant, TEL_A, "entrante", "cliente", "hola")
    await _agregar(tenant, TEL_A, "saliente", "asesor", "dime")
    async with _cliente(_app(tenant)) as c:
        r = await c.get(f"/api/v1/conversaciones/{cid}/mensajes")
    assert r.status_code == 200
    assert [(m["autor"], m["texto"]) for m in r.json()] == [("cliente", "hola"), ("asesor", "dime")]


async def test_listar_mensajes_inexistente_da_404(tenant):
    async with _cliente(_app(tenant)) as c:
        r = await c.get("/api/v1/conversaciones/99999/mensajes")
    assert r.status_code == 404


# --- tomar (takeover) -------------------------------------------------------
async def test_tomar_pasa_a_humano(tenant):
    # Conversación que el bot aún atiende (asegurada sin escalar).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        conv = await SqlConversacionRepository(s).asegurar(TEL_A)
        await s.commit()
        cid = conv.id
    async with _cliente(_app(tenant)) as c:
        r = await c.post(f"/api/v1/conversaciones/{cid}/tomar")
    assert r.status_code == 200 and r.json()["estado"] == "humano"


# --- responder --------------------------------------------------------------
async def test_responder_envia_y_persiste_autor_asesor(tenant):
    cid = await _escalar(tenant, TEL_A, "x")           # ya está en humano
    enviador = _EnviadorFake()
    async with _cliente(_app(tenant, enviador=enviador)) as c:
        r = await c.post(f"/api/v1/conversaciones/{cid}/responder", json={"texto": "Te atiendo yo."})
        assert r.status_code == 200
        assert r.json()["autor"] == "asesor" and r.json()["direccion"] == "saliente"
        # El texto salió por el enviador (Kapso) al teléfono del cliente…
        assert [to_texto[1:] for to_texto in enviador.envios] == [(TEL_A, "Te atiendo yo.")]
        # …y quedó en el hilo.
        hilo = (await c.get(f"/api/v1/conversaciones/{cid}/mensajes")).json()
        assert hilo[-1]["autor"] == "asesor" and hilo[-1]["texto"] == "Te atiendo yo."


async def test_responder_sin_humano_da_409(tenant):
    # Conversación en estado bot: no se puede responder sin tomarla.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        conv = await SqlConversacionRepository(s).asegurar(TEL_A)
        await s.commit()
        cid = conv.id
    enviador = _EnviadorFake()
    async with _cliente(_app(tenant, enviador=enviador)) as c:
        r = await c.post(f"/api/v1/conversaciones/{cid}/responder", json={"texto": "hola"})
    assert r.status_code == 409
    assert enviador.envios == []           # no se envió nada


async def test_responder_texto_vacio_da_422(tenant):
    cid = await _escalar(tenant, TEL_A, "x")
    async with _cliente(_app(tenant, enviador=_EnviadorFake())) as c:
        r = await c.post(f"/api/v1/conversaciones/{cid}/responder", json={"texto": ""})
    assert r.status_code == 422


# --- SSE --------------------------------------------------------------------
async def test_resolver_emite_evento_sse(tenant):
    cid = await _escalar(tenant, TEL_A, "x")
    queue = await event_hub.subscribe(tenant_id=8484, dsn=tenant.url)
    try:
        async with _cliente(_app(tenant)) as c:
            r = await c.post(f"/api/v1/conversaciones/{cid}/resolver")
        assert r.status_code == 200
        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        evento = json.loads(payload)
        assert evento["event"] == "conversacion_resuelta"
        assert evento["data"]["conversacion_id"] == cid
    finally:
        await event_hub.unsubscribe(8484, queue)


async def test_responder_emite_evento_conversacion_mensaje(tenant):
    cid = await _escalar(tenant, TEL_A, "x")
    queue = await event_hub.subscribe(tenant_id=8485, dsn=tenant.url)
    try:
        async with _cliente(_app(tenant, enviador=_EnviadorFake())) as c:
            r = await c.post(f"/api/v1/conversaciones/{cid}/responder", json={"texto": "voy"})
        assert r.status_code == 200
        # Puede llegar primero el 'conversacion_escalada' del escalar previo no (fue antes del subscribe);
        # el del responder es 'conversacion_mensaje'.
        payload = await asyncio.wait_for(queue.get(), timeout=5.0)
        evento = json.loads(payload)
        assert evento["event"] == "conversacion_mensaje"
        assert evento["data"]["cliente_telefono"] == TEL_A and evento["data"]["autor"] == "asesor"
    finally:
        await event_hub.unsubscribe(8485, queue)
