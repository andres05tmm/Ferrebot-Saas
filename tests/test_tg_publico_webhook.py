"""Flujo del webhook Telegram público (`apps/tg_publico/webhook.py`) con fakes: seguridad, dedup, tenancy.

Espeja `tests/test_wa_webhook.py`. Verifica: secret-token fail-closed (403 sin abrir el tenant), dedup
por (tenant, update_id), slug no mapeado → 200 sin abrir el tenant, updates no-texto / no-privados
ignorados (200), y que el `cliente_telefono` del Contexto SIEMPRE sale del payload (`tg:{chat_id}`).
Además la ruta FastAPI, el procesador (encolado) y el job del agente.
"""
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from ai.envelope import Contexto
from apps.tg_publico.ports import AccionTg, TgPublicoDeps
from apps.tg_publico.webhook import crear_router_tg_publico, manejar_update_tg, parsear_update_tg
from apps.tg_publico.wiring import JOB_AGENTE, ProcesadorTgAgente
from apps.tg_publico.ports import UpdateTgPublico
from core.tenancy.context import ResolvedTenant

SLUG = "sirius"
SECRET = "tg-webhook-secret"
CHAT_ID = 987654321
UPDATE_ID = 42

PAYLOAD = {
    "update_id": UPDATE_ID,
    "message": {
        "message_id": 5,
        "chat": {"id": CHAT_ID, "type": "private"},
        "from": {"id": CHAT_ID},
        "text": "Hola, ¿qué hay de almuerzo?",
    },
}


def _tenant(estado: str = "activa", id: int = 7) -> ResolvedTenant:
    return ResolvedTenant(
        id=id, slug=SLUG, nombre="Sirius", estado=estado, db_name="d",
        connection_url="postgresql://x/y",
    )


class _FakeResolver:
    def __init__(self, tenant):
        self.tenant = tenant
        self.pedidos = []

    async def por_slug(self, slug):
        self.pedidos.append(slug)
        return self.tenant


class _FakeSecretos:
    def __init__(self, secret=SECRET):
        self.secret = secret
        self.pedidos = []

    async def webhook_secret(self, empresa_id):
        self.pedidos.append(empresa_id)
        return self.secret


class _FakeDedup:
    def __init__(self, nuevo=True):
        self.nuevo = nuevo
        self.vistos = []
        self.desmarcados = []

    async def marcar_si_nuevo(self, tenant_id, update_id):
        self.vistos.append((tenant_id, update_id))
        return self.nuevo

    async def desmarcar(self, tenant_id, update_id):
        self.desmarcados.append((tenant_id, update_id))


class _FakeProcesar:
    def __init__(self):
        self.llamadas = []

    async def __call__(self, update, ctx):
        self.llamadas.append((update, ctx))


_SIN_TENANT = object()


def _deps(*, tenant=_SIN_TENANT, secret=SECRET, nuevo=True, procesar=None):
    proc = procesar or _FakeProcesar()
    resuelto = _tenant() if tenant is _SIN_TENANT else tenant
    deps = TgPublicoDeps(
        resolver=_FakeResolver(resuelto),
        secretos=_FakeSecretos(secret),
        dedup=_FakeDedup(nuevo),
        procesar=proc,
    )
    return deps, proc


async def _correr(deps, *, slug=SLUG, secret=SECRET, payload=None):
    return await manejar_update_tg(slug, secret, payload if payload is not None else PAYLOAD, deps)


# --- seguridad: secret-token ------------------------------------------------
async def test_secret_invalido_rechaza_403_y_no_procesa():
    deps, proc = _deps()
    res = await _correr(deps, secret="malo")
    assert res.accion == AccionTg.SECRET_INVALIDO and res.status == 403
    assert proc.llamadas == []
    assert deps.dedup.vistos == []            # no se llegó al dedup


async def test_sin_secret_configurado_rechaza_fail_closed():
    deps, proc = _deps(secret=None)
    res = await _correr(deps)
    assert res.accion == AccionTg.SECRET_INVALIDO and res.status == 403


async def test_secret_ausente_en_header_rechaza():
    deps, proc = _deps()
    res = await manejar_update_tg(SLUG, None, PAYLOAD, deps)
    assert res.accion == AccionTg.SECRET_INVALIDO and res.status == 403


# --- tenancy ----------------------------------------------------------------
async def test_slug_no_mapeado_da_200_sin_abrir_tenant():
    deps, proc = _deps(tenant=None)            # resolver devuelve None
    res = await _correr(deps)
    assert res.accion == AccionTg.NO_MAPEADO and res.status == 200
    assert deps.resolver.pedidos == [SLUG]
    assert deps.secretos.pedidos == []         # no se leyó el secret (no hay tenant)
    assert proc.llamadas == []


async def test_empresa_inactiva_da_200_sin_procesar():
    deps, proc = _deps(tenant=_tenant(estado="suspendida"))
    res = await _correr(deps)
    assert res.accion == AccionTg.EMPRESA_INACTIVA and res.status == 200
    assert deps.secretos.pedidos == []         # ni se valida el secret de una empresa inactiva
    assert proc.llamadas == []


# --- parseo: solo texto en privado ------------------------------------------
async def test_update_no_texto_se_ignora():
    payload = {"update_id": 1, "message": {"chat": {"id": CHAT_ID, "type": "private"}, "from": {"id": 1}}}
    deps, proc = _deps()
    res = await _correr(deps, payload=payload)
    assert res.accion == AccionTg.UPDATE_IGNORADO and res.status == 200
    assert proc.llamadas == []


async def test_update_en_grupo_se_ignora():
    payload = {
        "update_id": 1,
        "message": {"chat": {"id": -100, "type": "group"}, "from": {"id": 1}, "text": "hola"},
    }
    deps, proc = _deps()
    res = await _correr(deps, payload=payload)
    assert res.accion == AccionTg.UPDATE_IGNORADO and res.status == 200
    assert proc.llamadas == []


async def test_callback_query_se_ignora():
    payload = {"update_id": 1, "callback_query": {"id": "c1", "data": "x"}}
    deps, proc = _deps()
    res = await _correr(deps, payload=payload)
    assert res.accion == AccionTg.UPDATE_IGNORADO and res.status == 200


# --- dedup ------------------------------------------------------------------
async def test_dedup_update_repetido():
    deps, proc = _deps(nuevo=False)
    res = await _correr(deps)
    assert res.accion == AccionTg.DUPLICADO and res.status == 200
    assert deps.dedup.vistos == [(7, UPDATE_ID)]
    assert proc.llamadas == []


async def test_mismo_update_id_dos_veces_ignora_el_segundo():
    """Dedup real (SET NX sobre un set): el segundo update con el mismo id no se procesa."""
    class _DedupReal:
        def __init__(self):
            self.marcas = set()
        async def marcar_si_nuevo(self, tenant_id, update_id):
            clave = (tenant_id, update_id)
            if clave in self.marcas:
                return False
            self.marcas.add(clave)
            return True
        async def desmarcar(self, tenant_id, update_id):
            self.marcas.discard((tenant_id, update_id))

    proc = _FakeProcesar()
    deps = TgPublicoDeps(
        resolver=_FakeResolver(_tenant()), secretos=_FakeSecretos(),
        dedup=_DedupReal(), procesar=proc,
    )
    r1 = await _correr(deps)
    r2 = await _correr(deps)
    assert r1.accion == AccionTg.PROCESADO
    assert r2.accion == AccionTg.DUPLICADO
    assert len(proc.llamadas) == 1


async def test_encolado_fallido_desmarca_el_dedup_y_propaga():
    class _ProcesarBoom:
        async def __call__(self, update, ctx):
            raise RuntimeError("cola caída")

    deps, _ = _deps(procesar=_ProcesarBoom())
    with pytest.raises(RuntimeError):
        await _correr(deps)
    assert deps.dedup.vistos == [(7, UPDATE_ID)]
    assert deps.dedup.desmarcados == [(7, UPDATE_ID)]   # liberado: el reintento procesará


# --- éxito: Contexto con la identidad DEL PAYLOAD ---------------------------
async def test_procesado_construye_contexto_con_telefono_del_payload():
    deps, proc = _deps(tenant=_tenant(id=7))
    res = await _correr(deps)
    assert res.accion == AccionTg.PROCESADO and res.status == 200
    update, ctx = proc.llamadas[0]
    assert isinstance(ctx, Contexto)
    assert ctx.tenant_id == 7
    assert ctx.cliente_telefono == f"tg:{CHAT_ID}"      # SIEMPRE del payload (chat.id)
    assert ctx.origen == "telegram"
    assert update.texto == "Hola, ¿qué hay de almuerzo?"


# --- procesador del agente (encola con la identidad del update) -------------
async def test_procesador_agente_encola_el_turno():
    encolados = []
    async def fake_encolar(*args):
        encolados.append(args)
    proc = ProcesadorTgAgente(encolar=fake_encolar)
    update = UpdateTgPublico(update_id=UPDATE_ID, chat_id=CHAT_ID, texto="hola")
    ctx = Contexto(tenant_id=7, usuario_id=0, rol="cliente", origen="telegram",
                   cliente_telefono=f"tg:{CHAT_ID}")
    await proc(update, ctx)
    assert encolados == [(JOB_AGENTE, 7, CHAT_ID, "hola", UPDATE_ID)]


# --- ruta FastAPI -----------------------------------------------------------
async def test_ruta_webhook_responde_200_con_secret_valido():
    deps, proc = _deps()
    app = FastAPI()
    app.include_router(crear_router_tg_publico())
    app.state.tg_deps = deps
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cliente:
        r = await cliente.post(
            f"/tg-publico/{SLUG}", json=PAYLOAD,
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
        )
    assert r.status_code == 200 and r.json()["accion"] == "procesado"
    assert proc.llamadas and proc.llamadas[0][1].cliente_telefono == f"tg:{CHAT_ID}"


async def test_ruta_webhook_rechaza_secret_invalido():
    deps, _ = _deps()
    app = FastAPI()
    app.include_router(crear_router_tg_publico())
    app.state.tg_deps = deps
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cliente:
        r = await cliente.post(
            f"/tg-publico/{SLUG}", json=PAYLOAD,
            headers={"X-Telegram-Bot-Api-Secret-Token": "malo"},
        )
    assert r.status_code == 403


async def test_ruta_webhook_body_invalido_da_400():
    deps, _ = _deps()
    app = FastAPI()
    app.include_router(crear_router_tg_publico())
    app.state.tg_deps = deps
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as cliente:
        r = await cliente.post(
            f"/tg-publico/{SLUG}", content=b"no-es-json{",
            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
        )
    assert r.status_code == 400


# --- parseo directo ---------------------------------------------------------
def test_parsear_update_texto_privado():
    u = parsear_update_tg(PAYLOAD)
    assert u is not None and u.chat_id == CHAT_ID and u.update_id == UPDATE_ID and u.texto


def test_parsear_update_texto_vacio_es_none():
    payload = {"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "text": ""}}
    assert parsear_update_tg(payload) is None
