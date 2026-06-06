"""Entregable 4 — orquestador del turno (factory + TurnoHandler), todo con fakes: cero red, cero PG.

Pin del contrato del orquestador:
  - arma el historial desde la memoria y se lo pasa a `ejecutar_turno`; el system prompt incluye la
    fecha Colombia y el bloque de entidades cuando existen, y NO incluye catálogo/precios;
  - ruta feliz: responde por el Notificador con `RespuestaAgente.texto` y persiste user+assistant;
  - respaldo: si `ejecutar_turno` (proveedor) lanza, el usuario recibe un mensaje amable y NO se
    propaga la excepción;
  - token accounting: el handler envuelve el proveedor con `ProveedorMedido` ANTES de pasarlo a
    `ejecutar_turno` (el conteo vive en el wrapper; ver tests/test_proveedor_medido.py);
  - la factory arma un `Recursos` NUEVO por turno (no compartido entre llamadas).
"""
from datetime import date

from ai.agent import RespuestaAgente
from ai.envelope import Contexto
from ai.turno import (
    MENSAJE_RESPALDO,
    construir_system_prompt,
    crear_turno_handler,
)
from apps.bot.ports import UpdateBot
from core.config.timezone import today_co
from core.llm.base import Message
from core.llm.factory import LLMResuelto
from core.llm.medicion import ProveedorMedido
from modules.memoria.service import TIPO_ULTIMO_CLIENTE, TIPO_ULTIMO_PRODUCTO


# --------------------------------- fakes ----------------------------------

class FakeLLM:
    nombre = "fake"
    api_key = "k"

    async def generate(self, **kw):   # no se usa: ejecutar_turno va falseado
        raise AssertionError("no debería llamarse")


class FakeDispatcher:
    """Solo necesita resolver el proveedor; el bucle del agente va falseado (no usa ejecutar/catálogo)."""

    def __init__(self, model: str = "modelo-x") -> None:
        self.model = model
        self.selecciones: list[int] = []

    async def seleccionar_proveedor(self, empresa_id: int, *, turno=None) -> LLMResuelto:
        self.selecciones.append(empresa_id)
        return LLMResuelto(provider=FakeLLM(), model=self.model, provider_nombre="fake")


class FakeMemoria:
    def __init__(self, historial=None, entidades=None) -> None:
        self._historial = list(historial or [])
        self._entidades = dict(entidades or {})
        self.guardados: list[tuple[int, str, str]] = []
        self.recordadas: list[tuple[int, str, dict]] = []
        self.falla_guardar = False

    async def cargar_historial(self, chat_id, *, limite=8):
        return list(self._historial)

    async def leer_entidades(self, chat_id):
        return dict(self._entidades)

    async def guardar_turno(self, chat_id, *, usuario, asistente):
        if self.falla_guardar:
            raise RuntimeError("fallo al persistir")
        self.guardados.append((chat_id, usuario, asistente))

    async def recordar_entidad(self, chat_id, tipo, valor):
        self.recordadas.append((chat_id, tipo, valor))


class FakeCostos:
    def __init__(self, *, falla=False) -> None:
        self.llamadas: list[dict] = []
        self.falla = falla

    async def acumular(self, *, fecha, modelo, tokens_in, tokens_out):
        if self.falla:
            raise RuntimeError("fallo al acumular costos")
        self.llamadas.append(
            {"fecha": fecha, "modelo": modelo, "tokens_in": tokens_in, "tokens_out": tokens_out}
        )


class FakeNotificador:
    def __init__(self) -> None:
        self.enviados: list[tuple[int, str]] = []

    async def responder(self, chat_id: int, texto: str) -> None:
        self.enviados.append((chat_id, texto))


class FakeEjecutar:
    """Stand-in de `ai.agent.ejecutar_turno`: captura kwargs y devuelve (o lanza) lo pre-cargado."""

    def __init__(self, respuesta: RespuestaAgente | None = None, *, error: Exception | None = None):
        self._respuesta = respuesta
        self._error = error
        self.llamadas: list[dict] = []

    async def __call__(self, **kw) -> RespuestaAgente:
        self.llamadas.append(kw)
        if self._error is not None:
            raise self._error
        return self._respuesta


# --------------------------------- helpers --------------------------------

_SESSION = object()   # sesión sentinela del tenant (el handler la pasa tal cual)


def _ctx() -> Contexto:
    return Contexto(tenant_id=1, usuario_id=42, rol="vendedor", origen="bot",
                    capacidades=frozenset({"bot_telegram"}))


def _update(texto="2 martillo", chat_id=555) -> UpdateBot:
    return UpdateBot(update_id=100, chat_id=chat_id, telegram_id=555, texto=texto)


def _handler(*, memoria, costos, ejecutar, dispatcher=None, crear_recursos=None):
    dispatcher = dispatcher or FakeDispatcher()
    if crear_recursos is None:
        crear_recursos = lambda s: object()       # noqa: E731 — recurso fresco por llamada
    return crear_turno_handler(
        dispatcher=dispatcher,
        memoria=lambda s: memoria,
        costos=lambda s: costos,
        crear_recursos=crear_recursos,
        ejecutar=ejecutar,
    )


def _respuesta(texto="Listo, registrada.") -> RespuestaAgente:
    return RespuestaAgente(texto=texto, ruta="texto")


# ----------------------- system prompt (helper puro) ----------------------

def test_system_prompt_incluye_fecha_colombia():
    prompt = construir_system_prompt({})
    assert today_co().isoformat() in prompt


def test_system_prompt_fecha_inyectable():
    prompt = construir_system_prompt({}, hoy=date(2026, 1, 15))
    assert "2026-01-15" in prompt


def test_system_prompt_incluye_entidades_cuando_existen():
    entidades = {
        TIPO_ULTIMO_CLIENTE: {"id": 7, "nombre": "Juan Pérez"},
        TIPO_ULTIMO_PRODUCTO: {"id": 3, "nombre": "Martillo"},
    }
    prompt = construir_system_prompt(entidades)
    assert "Contexto reciente" in prompt
    assert "Juan Pérez" in prompt and "Martillo" in prompt


def test_system_prompt_sin_entidades_no_pone_contexto_reciente():
    prompt = construir_system_prompt({})
    assert "Contexto reciente" not in prompt


def test_system_prompt_no_incluye_catalogo_ni_precios():
    entidades = {TIPO_ULTIMO_PRODUCTO: {"id": 3, "nombre": "Martillo"}}
    prompt = construir_system_prompt(entidades).lower()
    assert "precio" not in prompt
    assert "catálogo" not in prompt and "catalogo" not in prompt


def test_system_prompt_pide_texto_plano_sin_markdown():
    prompt = construir_system_prompt({})
    assert "texto plano" in prompt.lower()
    assert "**" not in prompt              # la propia regla no debe traer Markdown


def test_system_prompt_pide_buscar_producto_por_nombre_base():
    prompt = construir_system_prompt({})
    assert "nombre base" in prompt.lower()


def test_system_prompt_prohibe_calcular_fracciones():
    prompt = construir_system_prompt({})
    bajo = prompt.lower()
    assert "consultar_producto" in bajo and "fracción" in bajo
    assert "precio" not in bajo                          # respeta el invariante del prompt
    assert "catálogo" not in bajo and "catalogo" not in bajo


# ----------------------- historial → ejecutar_turno -----------------------

async def test_pasa_historial_y_system_a_ejecutar_turno():
    historial = [
        Message(role="user", content="hola"),
        Message(role="assistant", content="¿en qué te ayudo?"),
    ]
    entidades = {TIPO_ULTIMO_CLIENTE: {"id": 7, "nombre": "Juan Pérez"}}
    memoria = FakeMemoria(historial=historial, entidades=entidades)
    ejecutar = FakeEjecutar(_respuesta())
    handler = _handler(memoria=memoria, costos=FakeCostos(), ejecutar=ejecutar)

    await handler(_update(), _ctx(), _SESSION, FakeNotificador())

    assert len(ejecutar.llamadas) == 1
    kw = ejecutar.llamadas[0]
    assert kw["texto"] == "2 martillo"
    assert kw["historial"] == historial
    assert "Juan Pérez" in kw["system"]              # entidades en el system prompt
    assert today_co().isoformat() in kw["system"]
    assert "precio" not in kw["system"].lower()      # sin catálogo/precios


# ------------------------------ ruta feliz --------------------------------

async def test_ruta_feliz_responde_y_persiste():
    memoria = FakeMemoria()
    notif = FakeNotificador()
    ejecutar = FakeEjecutar(_respuesta("Venta #1 registrada."))
    handler = _handler(memoria=memoria, costos=FakeCostos(), ejecutar=ejecutar)

    await handler(_update(), _ctx(), _SESSION, notif)

    assert notif.enviados == [(555, "Venta #1 registrada.")]
    assert memoria.guardados == [(555, "2 martillo", "Venta #1 registrada.")]


async def test_persistencia_best_effort_no_rompe_respuesta():
    memoria = FakeMemoria()
    memoria.falla_guardar = True
    notif = FakeNotificador()
    ejecutar = FakeEjecutar(_respuesta("ok"))
    handler = _handler(memoria=memoria, costos=FakeCostos(), ejecutar=ejecutar)

    await handler(_update(), _ctx(), _SESSION, notif)   # no debe propagar

    assert notif.enviados == [(555, "ok")]              # la respuesta ya salió


# ------------------------------- respaldo ---------------------------------

async def test_respaldo_ante_fallo_del_proveedor():
    memoria = FakeMemoria()
    notif = FakeNotificador()
    ejecutar = FakeEjecutar(error=TimeoutError("proveedor caído"))
    handler = _handler(memoria=memoria, costos=FakeCostos(), ejecutar=ejecutar)

    await handler(_update(), _ctx(), _SESSION, notif)   # NO propaga la excepción

    assert notif.enviados == [(555, MENSAJE_RESPALDO)]


# --------------------------- token accounting -----------------------------

async def test_handler_envuelve_proveedor_con_proveedor_medido():
    # El conteo de tokens vive en el wrapper (ProveedorMedido); aquí solo se verifica que el handler
    # envuelve el proveedor ANTES de pasarlo a ejecutar_turno. La acumulación se prueba en
    # tests/test_proveedor_medido.py.
    memoria = FakeMemoria()
    ejecutar = FakeEjecutar(_respuesta("ok"))
    handler = _handler(memoria=memoria, costos=FakeCostos(), ejecutar=ejecutar)

    await handler(_update(), _ctx(), _SESSION, FakeNotificador())

    proveedor = ejecutar.llamadas[0]["proveedor"]
    assert isinstance(proveedor.provider, ProveedorMedido)   # el provider que llega es el medido
    assert proveedor.model == "modelo-x"                     # el modelo resuelto se conserva


# ----------------------- factory: Recursos por turno ----------------------

async def test_factory_arma_recursos_nuevo_por_turno():
    memoria = FakeMemoria()
    ejecutar = FakeEjecutar(_respuesta())
    creados: list[object] = []

    def crear_recursos(session):
        r = object()
        creados.append(r)
        return r

    handler = _handler(
        memoria=memoria, costos=FakeCostos(), ejecutar=ejecutar, crear_recursos=crear_recursos
    )

    await handler(_update(), _ctx(), _SESSION, FakeNotificador())
    await handler(_update(), _ctx(), _SESSION, FakeNotificador())

    # un Recursos fresco por turno, y es el que recibió ejecutar_turno (no compartido)
    assert len(creados) == 2
    assert creados[0] is not creados[1]
    recursos_pasados = [kw["recursos"] for kw in ejecutar.llamadas]
    assert recursos_pasados == creados
