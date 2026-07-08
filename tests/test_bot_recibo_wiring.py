"""Wiring REAL del agente de obra en el bot (Stream B, Fase 6): parseo de la foto + adaptador de canal.

Verifica, SIN red ni BD:
  - `parsear_update` reconoce una foto (`message.photo`) → `foto_file_id` (mayor resolución) +
    `telegram_message_id`, y usa la leyenda (`caption`) como texto del turno;
  - `crear_preparar_recibo` (adaptador de canal): descarga la imagen, la sube al bucket (Cloudinary de
    la empresa) y la inyecta en `recursos.obra.canal` como `ContextoTelegram` (la imagen NUNCA es un
    arg del modelo); fail-open ante fallo de descarga/bucket; no-op para tenants sin el pack de obra;
  - `_crear_recursos_factory` adjunta `Recursos.obra` (ObraDeps) con el `resolver_vision` cableado.
"""
import base64
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

from ai.agent import RespuestaAgente
from ai.dispatcher import Recursos
from ai.envelope import Contexto
from ai.obra_tools import ContextoTelegram, ObraDeps
from ai.turno import crear_turno_handler
from apps.bot.ports import UpdateBot
from apps.bot.webhook import parsear_update
from apps.bot.wiring import (
    _TEXTO_RECIBO_DEFAULT,
    _crear_recursos_factory,
    crear_preparar_recibo,
)
from core.llm.factory import LLMResuelto
from modules.proveedores.cloudinary_config import CloudinaryCredenciales


# ------------------------------- fakes ------------------------------------
class _FakeArchivos:
    def __init__(self, data=b"JPEGBYTES", *, fail=False):
        self._data = data
        self._fail = fail
        self.pedido = None

    async def descargar(self, file_id):
        self.pedido = file_id
        if self._fail:
            raise RuntimeError("telegram caido")
        return self._data


class _Bundle:
    def __init__(self, archivos):
        self.archivos = archivos
        self.notificador = None
        self.transcriptor = None


class _FakeRecursosBot:
    def __init__(self, bundle):
        self._bundle = bundle
        self.pedido = None

    async def para(self, empresa_id):
        self.pedido = empresa_id
        return self._bundle


@asynccontextmanager
async def _abrir_control_dummy():
    yield SimpleNamespace()   # nunca se usa: cargar_config_cloudinary va monkeypatcheado


async def _no_vision(tenant_id):
    raise AssertionError("no debería resolver visión aquí")


def _ctx(caps=frozenset({"obras", "maquinaria"})):
    return Contexto(
        tenant_id=7, usuario_id=1, rol="vendedor", origen="bot",
        idempotency_key="k", capacidades=caps,
    )


def _recursos_con_obra():
    obra = ObraDeps(
        maquinaria=object(), obras=object(), caja=object(),
        resolver_vision=_no_vision, canal=ContextoTelegram(),
    )
    return Recursos(deps=None, catalogo=None, umbrales=None, obra=obra)


def _update_foto(foto="big", mid=88):
    return UpdateBot(
        update_id=30, chat_id=555, telegram_id=555,
        foto_file_id=foto, telegram_message_id=mid,
    )


# --------------------------- parseo de la foto ----------------------------
def test_parsear_update_reconoce_foto_y_caption():
    payload = {
        "update_id": 30,
        "message": {
            "message_id": 88, "from": {"id": 555}, "chat": {"id": 555},
            "caption": "imputa a Torre Norte",
            "photo": [{"file_id": "chica"}, {"file_id": "mediana"}, {"file_id": "grande"}],
        },
    }
    u = parsear_update(payload)
    assert u is not None
    assert u.foto_file_id == "grande"          # mayor resolución = última de la lista
    assert u.telegram_message_id == 88
    assert u.texto == "imputa a Torre Norte"    # la leyenda es el texto del turno
    assert u.voz_file_id is None


def test_parsear_update_foto_sin_caption():
    payload = {
        "update_id": 31,
        "message": {
            "message_id": 90, "from": {"id": 555}, "chat": {"id": 555},
            "photo": [{"file_id": "unica"}],
        },
    }
    u = parsear_update(payload)
    assert u is not None and u.foto_file_id == "unica" and u.texto is None


# --------------------- adaptador de canal (preparar_recibo) ----------------
async def test_preparar_recibo_inyecta_canal_sin_cloudinary(monkeypatch):
    async def _sin_cloud(cs, master, eid):
        return None                            # empresa sin Cloudinary
    monkeypatch.setattr("apps.bot.wiring.cargar_config_cloudinary", _sin_cloud)

    archivos = _FakeArchivos(data=b"XYZ")
    recursos_bot = _FakeRecursosBot(_Bundle(archivos))
    preparar = crear_preparar_recibo(recursos_bot, _abrir_control_dummy, "master-key")

    rec, texto = await preparar(_update_foto(), _ctx(), _recursos_con_obra(), None)

    canal = rec.obra.canal
    assert canal.imagen is not None
    assert canal.imagen.data == base64.b64encode(b"XYZ").decode("ascii")
    assert canal.imagen.media_type == "image/jpeg"
    assert canal.telegram_user_id == "555"
    assert canal.telegram_message_id == "88"    # ancla de idempotencia = message_id
    assert canal.comprobante_url is None        # sin bucket: solo imagen embebida
    assert texto == _TEXTO_RECIBO_DEFAULT        # foto sin leyenda → prompt sintético
    assert archivos.pedido == "big"
    assert recursos_bot.pedido == 7             # bundle de ESA empresa


async def test_preparar_recibo_sube_a_cloudinary(monkeypatch):
    async def _con_cred(cs, master, eid):
        return CloudinaryCredenciales(cloud_name="c", api_key="k", api_secret="s")
    monkeypatch.setattr("apps.bot.wiring.cargar_config_cloudinary", _con_cred)

    subidos = {}

    class _FakeCloud:
        def __init__(self, cred):
            self.cred = cred

        async def subir(self, data, *, filename=None):
            subidos["data"] = data
            subidos["filename"] = filename
            return "https://bucket/recibo.jpg"

    monkeypatch.setattr("apps.bot.wiring.CloudinaryClient", _FakeCloud)

    preparar = crear_preparar_recibo(
        _FakeRecursosBot(_Bundle(_FakeArchivos(data=b"IMG"))), _abrir_control_dummy, "m"
    )
    rec, _ = await preparar(_update_foto(mid=88), _ctx(), _recursos_con_obra(), None)

    assert rec.obra.canal.comprobante_url == "https://bucket/recibo.jpg"
    assert subidos["data"] == b"IMG" and subidos["filename"] == "recibo-88.jpg"


async def test_preparar_recibo_descarga_fallo_degrada(monkeypatch):
    async def _sin_cloud(cs, master, eid):
        return None
    monkeypatch.setattr("apps.bot.wiring.cargar_config_cloudinary", _sin_cloud)

    preparar = crear_preparar_recibo(
        _FakeRecursosBot(_Bundle(_FakeArchivos(fail=True))), _abrir_control_dummy, "m"
    )
    base = _recursos_con_obra()
    rec, texto = await preparar(_update_foto(), _ctx(), base, None)

    # Fail-open: el canal queda SIN imagen (la tool pedirá la foto), pero el turno sigue.
    assert rec.obra.canal.imagen is None
    assert texto == _TEXTO_RECIBO_DEFAULT


async def test_preparar_recibo_subida_fallo_conserva_imagen(monkeypatch):
    async def _con_cred(cs, master, eid):
        return CloudinaryCredenciales(cloud_name="c", api_key="k", api_secret="s")
    monkeypatch.setattr("apps.bot.wiring.cargar_config_cloudinary", _con_cred)

    class _CloudRoto:
        def __init__(self, cred):
            pass

        async def subir(self, data, *, filename=None):
            raise RuntimeError("bucket caido")

    monkeypatch.setattr("apps.bot.wiring.CloudinaryClient", _CloudRoto)

    preparar = crear_preparar_recibo(
        _FakeRecursosBot(_Bundle(_FakeArchivos(data=b"IMG"))), _abrir_control_dummy, "m"
    )
    rec, _ = await preparar(_update_foto(), _ctx(), _recursos_con_obra(), None)

    # El bucket es opcional: la imagen embebida sobrevive, comprobante_url queda None.
    assert rec.obra.canal.imagen is not None
    assert rec.obra.canal.comprobante_url is None


async def test_preparar_recibo_retail_es_noop():
    archivos = _FakeArchivos()
    preparar = crear_preparar_recibo(
        _FakeRecursosBot(_Bundle(archivos)), _abrir_control_dummy, "m"
    )
    base = _recursos_con_obra()
    # Tenant sin la capacidad `obras`: no se descarga ni se toca el canal; el texto queda igual.
    rec, texto = await preparar(_update_foto(), _ctx(caps=frozenset({"ventas"})), base, "hola")

    assert rec is base and texto == "hola"
    assert archivos.pedido is None


# --------------------- factory adjunta el pack de obra ---------------------
def test_factory_adjunta_obra_deps_con_resolver_vision():
    async def rv(tenant_id):
        return "LLM"

    crear = _crear_recursos_factory(config=SimpleNamespace(), resolver_vision=rv)
    rec = crear(MagicMock())     # sesión dummy: los repos solo la guardan (sin I/O al construir)

    assert isinstance(rec.obra, ObraDeps)
    assert rec.obra.resolver_vision is rv
    assert rec.obra.canal == ContextoTelegram()      # canal vacío; la foto se inyecta por turno
    assert rec.obra.caja is rec.deps.caja            # misma caja del turno (mueve la misma caja)


# ------------- handler: la rama de foto invoca preparar_recibo -------------
class _FakeMemoria:
    async def cargar_historial(self, chat_id, *, limite=8):
        return []

    async def leer_entidades(self, chat_id):
        return {}

    async def guardar_turno(self, chat_id, *, usuario, asistente):
        pass

    async def recordar_entidad(self, chat_id, tipo, valor):
        pass


class _FakeCostos:
    async def acumular(self, *, fecha, modelo, tokens_in, tokens_out):
        pass


class _FakeLLM:
    nombre = "fake"
    api_key = "k"

    async def generate(self, **kw):
        raise AssertionError("no debería llamarse: ejecutar va falseado")


class _FakeDispatcher:
    async def seleccionar_proveedor(self, empresa_id, *, turno=None):
        return LLMResuelto(provider=_FakeLLM(), model="m", provider_nombre="fake")


class _FakeNotificador:
    def __init__(self):
        self.enviados = []

    async def responder(self, chat_id, texto):
        self.enviados.append((chat_id, texto))


class _SpyEjecutar:
    def __init__(self):
        self.llamadas = []

    async def __call__(self, **kw):
        self.llamadas.append(kw)
        return RespuestaAgente(texto="ok", ruta="texto")


def _handler(preparar_recibo, ejecutar):
    return crear_turno_handler(
        dispatcher=_FakeDispatcher(),
        memoria=lambda s: _FakeMemoria(),
        costos=lambda s: _FakeCostos(),
        crear_recursos=lambda s: _recursos_con_obra(),
        ejecutar=ejecutar,
        preparar_recibo=preparar_recibo,
    )


async def test_handler_foto_invoca_preparar_recibo_y_pasa_texto_al_modelo():
    vistos = []

    async def _spy(update, ctx, recursos, texto):
        vistos.append((update.foto_file_id, texto))
        return recursos, "PROMPT-RECIBO"

    ejecutar = _SpyEjecutar()
    handler = _handler(_spy, ejecutar)
    update = UpdateBot(update_id=1, chat_id=555, telegram_id=555, foto_file_id="big", telegram_message_id=9)

    await handler(update, _ctx(), object(), _FakeNotificador())

    assert vistos == [("big", None)]                         # se invocó con la foto y sin texto previo
    assert ejecutar.llamadas and ejecutar.llamadas[0]["texto"] == "PROMPT-RECIBO"


async def test_handler_foto_sin_texto_resultante_no_llama_al_modelo():
    async def _preparar_none(update, ctx, recursos, texto):
        return recursos, None                                # p. ej. tenant sin el pack: no materializa

    ejecutar = _SpyEjecutar()
    handler = _handler(_preparar_none, ejecutar)
    update = UpdateBot(update_id=2, chat_id=555, telegram_id=555, foto_file_id="big")
    notif = _FakeNotificador()

    await handler(update, _ctx(), object(), notif)

    assert ejecutar.llamadas == []                            # se cortó barato, sin gastar el modelo
    assert notif.enviados == []
