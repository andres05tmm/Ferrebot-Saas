"""Camino FOTO del canal Telegram público: comprobante de pago del cliente.

Espeja el estilo de `tests/test_tg_publico_webhook.py` / `_jobs.py` (fakes, sin red/DB/Redis). Cubre:
webhook acepta una foto privada y encola con el `file_id` del tamaño MÁS grande; una foto en grupo se
ignora; el texto sigue encolando sin `file_id` (regresión); y el job de foto descarga + extrae con
visión + registra el comprobante (frente B, monkeypatch) + confirma al cliente y persiste el hilo del
inbox. Una extracción ilegible responde el mensaje amable fijo y NO crashea ni intenta registrar.
"""
from decimal import Decimal

from ai.vision.recibo import ReciboExtraido
from apps.tg_publico.jobs import atender_mensaje_tg
from apps.tg_publico.ports import AccionTg, TgPublicoDeps
from apps.tg_publico.webhook import _foto_mas_grande, manejar_update_tg, parsear_update_tg
from apps.tg_publico.wiring import JOB_AGENTE, ProcesadorTgAgente
from core.tenancy.context import ResolvedTenant

SLUG = "sirius"
SECRET = "tg-webhook-secret"
CHAT_ID = 987654321
TEL = f"tg:{CHAT_ID}"


def _tenant(estado: str = "activa", id: int = 7) -> ResolvedTenant:
    return ResolvedTenant(
        id=id, slug=SLUG, nombre="Sirius", estado=estado, db_name="d",
        connection_url="postgresql://x/y",
    )


PAYLOAD_FOTO = {
    "update_id": 43,
    "message": {
        "message_id": 6,
        "chat": {"id": CHAT_ID, "type": "private"},
        "from": {"id": CHAT_ID},
        "caption": "ya pagué",
        "photo": [
            {"file_id": "chica", "width": 90, "height": 60, "file_size": 1000},
            {"file_id": "grande", "width": 1280, "height": 720, "file_size": 90000},
        ],
    },
}


# --- fakes del webhook (espejo de test_tg_publico_webhook) -------------------
class _FakeResolver:
    def __init__(self, tenant):
        self.tenant = tenant

    async def por_slug(self, slug):
        return self.tenant


class _FakeSecretos:
    async def webhook_secret(self, empresa_id):
        return SECRET


class _FakeDedup:
    async def marcar_si_nuevo(self, tenant_id, update_id):
        return True

    async def desmarcar(self, tenant_id, update_id):
        pass


# --- webhook: parseo de la foto ---------------------------------------------
def test_foto_mas_grande_toma_el_de_mayor_peso():
    fotos = [
        {"file_id": "chica", "file_size": 100},
        {"file_id": "grande", "file_size": 9999},
        {"file_id": "media", "file_size": 500},
    ]
    assert _foto_mas_grande(fotos) == "grande"


def test_foto_mas_grande_sin_file_size_usa_area():
    fotos = [
        {"file_id": "chica", "width": 10, "height": 10},
        {"file_id": "grande", "width": 100, "height": 100},
    ]
    assert _foto_mas_grande(fotos) == "grande"


def test_parsear_foto_privada_toma_caption_y_file_id_mas_grande():
    u = parsear_update_tg(PAYLOAD_FOTO)
    assert u is not None
    assert u.foto_file_id == "grande"      # el tamaño más grande
    assert u.texto == "ya pagué"           # caption
    assert u.chat_id == CHAT_ID and u.update_id == 43


def test_parsear_foto_sin_caption_texto_vacio():
    payload = {
        "update_id": 1,
        "message": {"chat": {"id": CHAT_ID, "type": "private"}, "photo": [{"file_id": "x", "file_size": 5}]},
    }
    u = parsear_update_tg(payload)
    assert u is not None and u.foto_file_id == "x" and u.texto == ""


def test_parsear_foto_en_grupo_se_ignora():
    payload = {
        "update_id": 1,
        "message": {"chat": {"id": -100, "type": "group"}, "photo": [{"file_id": "x", "file_size": 5}]},
    }
    assert parsear_update_tg(payload) is None


# --- webhook: encola con file_id (foto) y sin él (texto, regresión) ---------
async def test_webhook_foto_encola_con_el_file_id_mas_grande():
    encolados = []

    async def fake_encolar(*args):
        encolados.append(args)

    deps = TgPublicoDeps(
        resolver=_FakeResolver(_tenant()), secretos=_FakeSecretos(),
        dedup=_FakeDedup(), procesar=ProcesadorTgAgente(encolar=fake_encolar),
    )
    res = await manejar_update_tg(SLUG, SECRET, PAYLOAD_FOTO, deps)
    assert res.accion == AccionTg.PROCESADO and res.status == 200
    # foto → 6 args, el último es el file_id del tamaño más grande; el caption viaja como texto.
    assert encolados == [(JOB_AGENTE, 7, CHAT_ID, "ya pagué", 43, "grande")]


async def test_webhook_texto_encola_sin_file_id_regresion():
    encolados = []

    async def fake_encolar(*args):
        encolados.append(args)

    payload = {
        "update_id": 42,
        "message": {"chat": {"id": CHAT_ID, "type": "private"}, "from": {"id": CHAT_ID}, "text": "hola"},
    }
    deps = TgPublicoDeps(
        resolver=_FakeResolver(_tenant()), secretos=_FakeSecretos(),
        dedup=_FakeDedup(), procesar=ProcesadorTgAgente(encolar=fake_encolar),
    )
    res = await manejar_update_tg(SLUG, SECRET, payload, deps)
    assert res.accion == AccionTg.PROCESADO
    assert encolados == [(JOB_AGENTE, 7, CHAT_ID, "hola", 42)]   # 5-tuple, sin foto


# --- job foto: descarga + visión + registro + respuesta + hilo del inbox ----
class _FakeRepo:
    """Repo de conversación falso: registra los mensajes persistidos en una lista compartida."""

    def __init__(self, mensajes, session):
        self._m = mensajes

    async def asegurar(self, telefono):
        self._m.append(("asegurar", telefono))

    async def agregar_mensaje(self, telefono, direccion, autor, texto):
        self._m.append((direccion, autor, texto))


def _fake_session_factory():
    async def _tenant_session(tenant):
        yield object()   # sesión sentinela: los repos están fakeados
    return _tenant_session


class _LLMResuelto:
    provider = object()
    model = "modelo-vision"


def _patch_persistencia(monkeypatch, jobs, mensajes):
    monkeypatch.setattr(jobs, "tenant_session", _fake_session_factory())
    monkeypatch.setattr(jobs, "SqlConversacionRepository", lambda s: _FakeRepo(mensajes, s))


async def test_job_foto_extrae_registra_y_responde(monkeypatch):
    import apps.tg_publico.jobs as jobs

    tenant = _tenant(7)
    llamadas = {}
    mensajes = []
    respondidos = []

    async def _resolver(tid):
        return tenant

    async def _descargar(tenant_id, file_id):
        llamadas["descarga"] = (tenant_id, file_id)
        return b"\xff\xd8jpeg"

    async def _vision(tenant_id):
        return _LLMResuelto()

    async def _extraer(imagen, provider, *, modelo):
        llamadas["extraer"] = (imagen.media_type, modelo)
        return ReciboExtraido(valor=Decimal("30000"), confianza=Decimal("0.95"))

    class _Resultado:
        estado = "asociado"
        cobro = None
        mensaje_cliente = "¡Pago recibido! ✅ Tu pedido va a cocina."

    async def _registrar(session, *, cliente_telefono, datos, imagen_ref):
        llamadas["registrar"] = (cliente_telefono, datos, imagen_ref)
        return _Resultado()

    async def _responder(tenant_id, telefono, texto):
        respondidos.append((tenant_id, telefono, texto))

    monkeypatch.setattr(jobs, "_descargar_foto_tg", _descargar)
    monkeypatch.setattr(jobs, "_resolver_vision", _vision)
    monkeypatch.setattr(jobs, "extraer_recibo", _extraer)
    monkeypatch.setattr(jobs, "_registrar_comprobante", lambda: _registrar)
    monkeypatch.setattr(jobs, "_responder", _responder)
    _patch_persistencia(monkeypatch, jobs, mensajes)

    ctx = {"resolver_tenant": _resolver, "tg_agente": None}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "ya pagué", 43, foto_file_id="grande")

    assert res == "comprobante"
    assert llamadas["descarga"] == (7, "grande")
    assert llamadas["extraer"] == ("image/jpeg", "modelo-vision")
    # registrar recibió la identidad DEL PAYLOAD y el file_id como imagen_ref
    tel_r, datos_r, ref_r = llamadas["registrar"]
    assert tel_r == TEL and ref_r == "grande" and datos_r.valor == Decimal("30000")
    # confirmación al cliente con el mensaje YA redactado por el frente B
    assert respondidos == [(7, TEL, "¡Pago recibido! ✅ Tu pedido va a cocina.")]
    # hilo del inbox: entrante comprobante (+caption) y la respuesta del bot
    assert ("entrante", "cliente", "[📎 comprobante de pago]\nya pagué") in mensajes
    assert mensajes[-1] == ("saliente", "bot", "¡Pago recibido! ✅ Tu pedido va a cocina.")


async def test_job_foto_ilegible_responde_amable_y_no_registra(monkeypatch):
    import apps.tg_publico.jobs as jobs

    tenant = _tenant(7)
    mensajes = []
    respondidos = []
    registrar_intentos = []

    async def _resolver(tid):
        return tenant

    async def _descargar(tenant_id, file_id):
        raise RuntimeError("telegram caído")   # la descarga falla → visión no corre

    async def _responder(tenant_id, telefono, texto):
        respondidos.append((tenant_id, telefono, texto))

    monkeypatch.setattr(jobs, "_descargar_foto_tg", _descargar)
    monkeypatch.setattr(jobs, "_registrar_comprobante", lambda: registrar_intentos.append(True))
    monkeypatch.setattr(jobs, "_responder", _responder)
    _patch_persistencia(monkeypatch, jobs, mensajes)

    ctx = {"resolver_tenant": _resolver, "tg_agente": None}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "", 44, foto_file_id="grande")

    assert res == "comprobante"                       # no crashea
    assert registrar_intentos == []                   # datos None → no se intenta registrar
    assert respondidos == [(7, TEL, jobs._MSG_ILEGIBLE)]
    assert mensajes[0] == ("asegurar", TEL)
    assert mensajes[1] == ("entrante", "cliente", "[📎 comprobante de pago]")   # sin caption
    assert mensajes[-1] == ("saliente", "bot", jobs._MSG_ILEGIBLE)


async def test_job_foto_registro_falla_cae_a_mensaje_amable(monkeypatch):
    """Si el frente B lanza, no tumba el job: responde el mensaje amable fijo."""
    import apps.tg_publico.jobs as jobs

    tenant = _tenant(7)
    mensajes = []
    respondidos = []

    async def _resolver(tid):
        return tenant

    async def _descargar(tenant_id, file_id):
        return b"jpeg"

    async def _vision(tenant_id):
        return _LLMResuelto()

    async def _extraer(imagen, provider, *, modelo):
        return ReciboExtraido(valor=Decimal("30000"), confianza=Decimal("0.9"))

    async def _registrar(session, *, cliente_telefono, datos, imagen_ref):
        raise RuntimeError("frente B roto")

    async def _responder(tenant_id, telefono, texto):
        respondidos.append((tenant_id, telefono, texto))

    monkeypatch.setattr(jobs, "_descargar_foto_tg", _descargar)
    monkeypatch.setattr(jobs, "_resolver_vision", _vision)
    monkeypatch.setattr(jobs, "extraer_recibo", _extraer)
    monkeypatch.setattr(jobs, "_registrar_comprobante", lambda: _registrar)
    monkeypatch.setattr(jobs, "_responder", _responder)
    _patch_persistencia(monkeypatch, jobs, mensajes)

    ctx = {"resolver_tenant": _resolver, "tg_agente": None}
    res = await atender_mensaje_tg(ctx, 7, CHAT_ID, "", 45, foto_file_id="grande")

    assert res == "comprobante"
    assert respondidos == [(7, TEL, jobs._MSG_ILEGIBLE)]
