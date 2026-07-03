"""Entregable 1 — webhook del bot: seguridad, tenancy y armado de Contexto (con fakes, sin BD).

Pin del contrato del orquestador `manejar_update` y del parseo del update. Lo crítico que estos
tests fijan:
  - El secret-token se valida ANTES de abrir la base del tenant (un secret malo no toca Postgres).
  - Empresa desconocida → 404; inactiva → 403; ambas sin sesión de negocio.
  - Dedup por update_id: un reintento del webhook no se procesa dos veces.
  - telegram_id no mapeado / usuario inactivo → "no autorizado", sin mutar (vía bundle.notificador).
  - Turno válido → Contexto poblado (tenant, usuario, rol, capacidades, origen=bot) e
    idempotency_key determinista atada a (tenant, update_id).
  - La ruta FastAPI mapea ResultadoWebhook → status HTTP.

CR-3a: el webhook ya no recibe un `notificador` único; pide a `deps.recursos.para(tenant.id)` el
bundle de la empresa y usa `bundle.notificador` (multi-empresa, un bot-token por empresa).
"""
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from apps.bot.ports import Accion, BotDeps, UsuarioBot
from apps.bot.webhook import crear_app_bot, manejar_update, parsear_update
from core.tenancy.context import ResolvedTenant

SECRET = "s3cr3t-webhook"


# --------------------------------- fakes ----------------------------------

class FakeResolver:
    def __init__(self, tenants: dict[str, ResolvedTenant]):
        self._t = tenants

    async def por_slug(self, slug: str):
        return self._t.get(slug)


class FakeSecretos:
    def __init__(self, secret: str | None = SECRET, token: str | None = "bot-token"):
        self._secret, self._token = secret, token

    async def webhook_secret(self, empresa_id: int):
        return self._secret

    async def bot_token(self, empresa_id: int):
        return self._token


class FakeCapacidades:
    def __init__(self, caps):
        self._caps = frozenset(caps)

    async def efectivas(self, empresa_id: int):
        return self._caps


class FakeDedup:
    def __init__(self, ya_vistos=()):
        self.vistos = set(ya_vistos)            # set de (tenant_id, update_id)
        self.desmarcados: list[tuple[int, int]] = []

    async def marcar_si_nuevo(self, tenant_id: int, update_id: int) -> bool:
        clave = (tenant_id, update_id)
        if clave in self.vistos:
            return False
        self.vistos.add(clave)
        return True

    async def desmarcar(self, tenant_id: int, update_id: int) -> None:
        self.vistos.discard((tenant_id, update_id))
        self.desmarcados.append((tenant_id, update_id))


class FakeNotificador:
    def __init__(self):
        self.enviados: list[tuple[int, str]] = []

    async def responder(self, chat_id: int, texto: str) -> None:
        self.enviados.append((chat_id, texto))


class _FakeBundle:
    """Bundle por empresa: aquí el webhook solo usa `notificador` (voz/archivos son del turno)."""

    def __init__(self, notificador: FakeNotificador):
        self.notificador = notificador
        self.transcriptor = None
        self.archivos = None


class FakeRecursosBot:
    """`RecursosBot` falso: `para(empresa_id)` devuelve el bundle y registra la empresa pedida."""

    def __init__(self, notificador: FakeNotificador | None = None):
        self.notificador = notificador or FakeNotificador()
        self._bundle = _FakeBundle(self.notificador)
        self.empresas: list[int] = []

    async def para(self, empresa_id: int) -> _FakeBundle:
        self.empresas.append(empresa_id)
        return self._bundle


class FakeUsuariosRepo:
    def __init__(self, por_tid: dict[int, UsuarioBot]):
        self._m = por_tid

    async def por_telegram_id(self, telegram_id: int):
        return self._m.get(telegram_id)


class _FakeSession:
    """Sesión sentinela: el repo fake la ignora; solo se verifica identidad en `procesar`."""


class FakeSesionTenant:
    """CM de sesión que cuenta entradas/salidas (para probar que un secret malo NO la abre)."""

    def __init__(self):
        self.session = _FakeSession()
        self.entradas = 0
        self.salidas = 0
        self.tenant_visto = None

    def __call__(self, tenant):
        @asynccontextmanager
        async def _cm():
            self.entradas += 1
            self.tenant_visto = tenant
            try:
                yield self.session
            finally:
                self.salidas += 1

        return _cm()


class SpyProcesar:
    def __init__(self):
        self.llamadas: list[tuple] = []

    async def __call__(self, update, ctx, session, notificador):
        self.llamadas.append((update, ctx, session, notificador))


# --------------------------------- helpers --------------------------------

def _tenant(*, id=1, slug="puntorojo", estado="activa") -> ResolvedTenant:
    return ResolvedTenant(
        id=id, slug=slug, nombre="Punto Rojo", estado=estado, db_name="db",
        connection_url="postgresql://u:p@h/db",
    )


def make_deps(**kw) -> BotDeps:
    repo = kw.pop("usuarios_repo", None) or FakeUsuariosRepo(
        {555: UsuarioBot(id=42, rol="vendedor", activo=True)}
    )
    base = dict(
        resolver=FakeResolver({"puntorojo": _tenant()}),
        secretos=FakeSecretos(),
        capacidades=FakeCapacidades({"fiados", "bot_telegram"}),
        dedup=FakeDedup(),
        abrir_sesion=FakeSesionTenant(),
        recursos=FakeRecursosBot(),
        procesar=SpyProcesar(),
    )
    base.update(kw)
    return BotDeps(usuarios=lambda s: repo, **base)


def _payload_texto(update_id=100, chat_id=555, telegram_id=555, texto="2 martillo"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1, "from": {"id": telegram_id},
            "chat": {"id": chat_id}, "text": texto,
        },
    }


def _payload_voz(update_id=101, chat_id=555, telegram_id=555, file_id="AwACVoiceFileId"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 2, "from": {"id": telegram_id}, "chat": {"id": chat_id},
            "voice": {"file_id": file_id, "duration": 3},
        },
    }


# --------------------------- parseo del update ----------------------------

def test_parsea_mensaje_de_texto():
    u = parsear_update(_payload_texto(update_id=7, chat_id=99, telegram_id=99, texto="hola"))
    assert u is not None
    assert (u.update_id, u.chat_id, u.telegram_id, u.texto) == (7, 99, 99, "hola")
    assert u.voz_file_id is None


def test_parsea_nota_de_voz():
    u = parsear_update(_payload_voz(update_id=8, file_id="VOICE123"))
    assert u is not None
    assert u.voz_file_id == "VOICE123"
    assert u.texto is None


def test_parseo_ignora_update_sin_mensaje():
    assert parsear_update({"update_id": 9}) is None


def test_parseo_ignora_mensaje_editado():
    payload = {"update_id": 10, "edited_message": {"from": {"id": 1}, "chat": {"id": 1}, "text": "x"}}
    assert parsear_update(payload) is None


# ------------------------ seguridad: secret-token -------------------------

async def test_secret_invalido_no_abre_sesion_tenant():
    sesion = FakeSesionTenant()
    procesar = SpyProcesar()
    deps = make_deps(abrir_sesion=sesion, procesar=procesar)

    res = await manejar_update("puntorojo", "secret-equivocado", _payload_texto(), deps)

    assert res.accion is Accion.SECRET_INVALIDO
    assert res.status == 403
    assert sesion.entradas == 0          # nunca se tocó la base del tenant
    assert procesar.llamadas == []


async def test_secret_ausente_rechaza():
    sesion = FakeSesionTenant()
    deps = make_deps(abrir_sesion=sesion)

    res = await manejar_update("puntorojo", None, _payload_texto(), deps)

    assert res.accion is Accion.SECRET_INVALIDO
    assert res.status == 403
    assert sesion.entradas == 0


async def test_empresa_sin_secret_configurado_rechaza_fail_closed():
    # Empresa sin webhook_secret en el control DB → fail-CLOSED (no fail-open como FerreBot).
    sesion = FakeSesionTenant()
    deps = make_deps(secretos=FakeSecretos(secret=None), abrir_sesion=sesion)

    res = await manejar_update("puntorojo", "cualquier-cosa", _payload_texto(), deps)

    assert res.accion is Accion.SECRET_INVALIDO
    assert res.status == 403
    assert sesion.entradas == 0


# ----------------------- resolución de la empresa -------------------------

async def test_empresa_desconocida_404():
    sesion = FakeSesionTenant()
    deps = make_deps(resolver=FakeResolver({}), abrir_sesion=sesion)

    res = await manejar_update("noexiste", SECRET, _payload_texto(), deps)

    assert res.accion is Accion.EMPRESA_NO_ENCONTRADA
    assert res.status == 404
    assert sesion.entradas == 0


async def test_empresa_inactiva_403():
    inactiva = {"puntorojo": _tenant(estado="suspendida")}
    sesion = FakeSesionTenant()
    deps = make_deps(resolver=FakeResolver(inactiva), abrir_sesion=sesion)

    res = await manejar_update("puntorojo", SECRET, _payload_texto(), deps)

    assert res.accion is Accion.EMPRESA_INACTIVA
    assert res.status == 403
    assert sesion.entradas == 0


# ------------------------------- dedup ------------------------------------

async def test_update_duplicado_no_se_reprocesa():
    dedup = FakeDedup(ya_vistos={(1, 100)})   # (tenant_id=1, update_id=100) ya visto
    sesion = FakeSesionTenant()
    procesar = SpyProcesar()
    deps = make_deps(dedup=dedup, abrir_sesion=sesion, procesar=procesar)

    res = await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), deps)

    assert res.accion is Accion.DUPLICADO
    assert res.status == 200
    assert sesion.entradas == 0          # un duplicado no abre la sesión del tenant
    assert procesar.llamadas == []


async def test_fallo_de_procesamiento_no_quema_el_dedup():
    # Si el turno falla (BD caída, LLM colgado), la marca de dedup se borra: el reintento
    # del webhook de Telegram procesa el mensaje (at-least-once; el doble procesamiento en
    # la ventana de carrera lo cubre la idempotency_key de dominio).
    class ProcesarQueFalla:
        async def __call__(self, update, ctx, session, notificador):
            raise RuntimeError("BD caída")

    dedup = FakeDedup()
    deps = make_deps(dedup=dedup, procesar=ProcesarQueFalla())

    with pytest.raises(RuntimeError):
        await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), deps)

    assert (1, 100) not in dedup.vistos
    assert dedup.desmarcados == [(1, 100)]

    # El reintento del proveedor SÍ procesa (mismo dedup, procesamiento sano).
    procesar = SpyProcesar()
    deps_reintento = make_deps(dedup=dedup, procesar=procesar)
    res = await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), deps_reintento)

    assert res.accion is Accion.PROCESADO
    assert len(procesar.llamadas) == 1


async def test_fallo_al_desmarcar_no_enmascara_el_error_original():
    # Si Redis también falla al desmarcar, el error original del turno sigue subiendo (500 →
    # Telegram reintenta); el DEL fallido solo se loguea.
    class ProcesarQueFalla:
        async def __call__(self, update, ctx, session, notificador):
            raise RuntimeError("BD caída")

    class DedupDesmarcarRoto(FakeDedup):
        async def desmarcar(self, tenant_id: int, update_id: int) -> None:
            raise ConnectionError("redis caído")

    deps = make_deps(dedup=DedupDesmarcarRoto(), procesar=ProcesarQueFalla())

    with pytest.raises(RuntimeError, match="BD caída"):
        await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), deps)


async def test_callback_sin_update_id_se_ignora_sin_error():
    # Un callback_query sin `update_id` en el nivel superior no debe reventar con KeyError (500):
    # se trata como update ignorado, sin tocar dedup ni la sesión del tenant.
    dedup = FakeDedup()
    sesion = FakeSesionTenant()
    procesar_cb = SpyProcesar()
    deps = make_deps(dedup=dedup, abrir_sesion=sesion, procesar_callback=procesar_cb)
    payload = {
        "callback_query": {
            "id": "cb-1", "from": {"id": 555},
            "message": {"chat": {"id": 555}}, "data": "pago:efectivo",
        },
    }

    res = await manejar_update("puntorojo", SECRET, payload, deps)

    assert res.accion is Accion.UPDATE_IGNORADO
    assert res.status == 200
    assert dedup.vistos == set()
    assert sesion.entradas == 0
    assert procesar_cb.llamadas == []


# -------------------------- autorización del usuario ----------------------

async def test_telegram_id_no_mapeado_no_autorizado():
    procesar = SpyProcesar()
    recursos = FakeRecursosBot()
    deps = make_deps(
        usuarios_repo=FakeUsuariosRepo({}),   # ningún usuario mapeado
        procesar=procesar, recursos=recursos,
    )

    res = await manejar_update("puntorojo", SECRET, _payload_texto(), deps)

    assert res.accion is Accion.NO_AUTORIZADO
    assert res.status == 200
    assert procesar.llamadas == []                 # no se ejecuta el turno
    assert len(recursos.notificador.enviados) == 1  # se responde por el notificador de la empresa


async def test_usuario_inactivo_no_autorizado():
    repo = FakeUsuariosRepo({555: UsuarioBot(id=42, rol="vendedor", activo=False)})
    procesar = SpyProcesar()
    deps = make_deps(usuarios_repo=repo, procesar=procesar)

    res = await manejar_update("puntorojo", SECRET, _payload_texto(), deps)

    assert res.accion is Accion.NO_AUTORIZADO
    assert procesar.llamadas == []


# --------------------------- turno válido (Contexto) ----------------------

async def test_turno_valido_arma_contexto_y_delega():
    sesion = FakeSesionTenant()
    procesar = SpyProcesar()
    recursos = FakeRecursosBot()
    deps = make_deps(
        capacidades=FakeCapacidades({"fiados", "bot_telegram"}),
        abrir_sesion=sesion, procesar=procesar, recursos=recursos,
    )

    res = await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), deps)

    assert res.accion is Accion.PROCESADO
    assert res.status == 200
    ctx = res.ctx
    assert ctx is not None
    assert ctx.tenant_id == 1
    assert ctx.usuario_id == 42
    assert ctx.rol == "vendedor"
    assert ctx.origen == "bot"
    assert ctx.capacidades == frozenset({"fiados", "bot_telegram"})
    assert ctx.idempotency_key                      # no vacío
    assert ctx.request_id                            # no vacío

    # el bundle se resolvió para la empresa resuelta (un bot-token por empresa)
    assert recursos.empresas == [1]
    # delega el turno con el MISMO contexto, la sesión del tenant y el notificador del bundle
    assert len(procesar.llamadas) == 1
    update, ctx_pasado, session_pasada, notif_pasado = procesar.llamadas[0]
    assert ctx_pasado is ctx
    assert session_pasada is sesion.session
    assert notif_pasado is recursos.notificador
    assert update.update_id == 100


async def test_request_id_explicito_viaja_al_contexto():
    deps = make_deps()
    res = await manejar_update("puntorojo", SECRET, _payload_texto(), deps, request_id="rid-123")
    assert res.ctx is not None
    assert res.ctx.request_id == "rid-123"


# ------------------ idempotency_key atada a (tenant, update_id) -----------

async def test_idempotency_key_determinista_por_update_id():
    # misma empresa + mismo update_id (dedup fresco cada vez) → misma key
    r1 = await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), make_deps())
    r2 = await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), make_deps())
    assert r1.ctx.idempotency_key == r2.ctx.idempotency_key

    # distinto update_id → distinta key
    r3 = await manejar_update("puntorojo", SECRET, _payload_texto(update_id=101), make_deps())
    assert r3.ctx.idempotency_key != r1.ctx.idempotency_key


async def test_idempotency_key_distinta_entre_empresas():
    deps_a = make_deps(resolver=FakeResolver({"puntorojo": _tenant(id=1)}))
    deps_b = make_deps(resolver=FakeResolver({"puntorojo": _tenant(id=2)}))
    ra = await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), deps_a)
    rb = await manejar_update("puntorojo", SECRET, _payload_texto(update_id=100), deps_b)
    assert ra.ctx.idempotency_key != rb.ctx.idempotency_key   # mismo update_id, distinta empresa


# ----------------------------- update ignorado ----------------------------

async def test_update_sin_mensaje_se_ignora_sin_tocar_tenant():
    sesion = FakeSesionTenant()
    procesar = SpyProcesar()
    deps = make_deps(abrir_sesion=sesion, procesar=procesar)

    res = await manejar_update("puntorojo", SECRET, {"update_id": 100}, deps)

    assert res.accion is Accion.UPDATE_IGNORADO
    assert res.status == 200
    assert sesion.entradas == 0
    assert procesar.llamadas == []


# --------------------------- ruta FastAPI (HTTP) --------------------------

def test_ruta_webhook_mapea_status_http():
    client = TestClient(crear_app_bot(make_deps()))
    headers_ok = {"X-Telegram-Bot-Api-Secret-Token": SECRET}

    assert client.post("/tg/puntorojo", json=_payload_texto(), headers=headers_ok).status_code == 200
    assert client.post(
        "/tg/puntorojo", json=_payload_texto(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "malo"},
    ).status_code == 403
    assert client.post("/tg/otra", json=_payload_texto(), headers=headers_ok).status_code == 404


def test_ruta_webhook_body_invalido_es_400():
    client = TestClient(crear_app_bot(make_deps()))
    headers_ok = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    # cuerpo no-JSON → 400 (superficie pública), no 500
    r = client.post("/tg/puntorojo", content="no-es-json", headers=headers_ok)
    assert r.status_code == 400
    # JSON válido pero no-objeto (lista) → 400
    assert client.post("/tg/puntorojo", json=[1, 2, 3], headers=headers_ok).status_code == 400


def test_health_acepta_get_y_head():
    # UptimeRobot (free) solo pinguea con HEAD: /health del bot debe responder 200 a ambos (no 405).
    client = TestClient(crear_app_bot(make_deps()))
    r_get = client.get("/health")
    assert r_get.status_code == 200
    assert r_get.json() == {"status": "ok"}
    assert client.head("/health").status_code == 200
