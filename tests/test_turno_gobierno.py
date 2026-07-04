"""Integración del gobierno (ADR 0024) en el turno del bot: la compuerta corre ANTES del modelo.

Invariante crítico (test-primero): con el presupuesto excedido, el turno se CORTA sin resolver el
proveedor ni entrar al bucle del agente (el modelo NO se llama — 0 llamadas), y el usuario recibe un
mensaje amable. Con cupo, el turno corre normal.
"""
from ai.agent import RespuestaAgente
from ai.envelope import Contexto
from ai.turno import crear_turno_handler
from apps.bot.ports import UpdateBot
from core.llm.factory import LLMResuelto
from core.llm.gobierno import MENSAJE_PRESUPUESTO, Gobierno, PoliticaGobierno


class FakeLLM:
    nombre = "fake"
    api_key = "k"

    async def generate(self, **kw):
        raise AssertionError("el modelo no debe llamarse cuando el gobierno corta")


class FakeDispatcher:
    def __init__(self) -> None:
        self.selecciones: list[int] = []

    async def seleccionar_proveedor(self, empresa_id, *, turno=None) -> LLMResuelto:
        self.selecciones.append(empresa_id)
        return LLMResuelto(provider=FakeLLM(), model="m", provider_nombre="fake")


class FakeMemoria:
    async def cargar_historial(self, chat_id, *, limite=8):
        return []

    async def leer_entidades(self, chat_id):
        return {}

    async def guardar_turno(self, chat_id, *, usuario, asistente):
        pass


class FakeCostos:
    async def acumular(self, *, fecha, modelo, tokens_in, tokens_out):
        pass


class FakeNotificador:
    def __init__(self) -> None:
        self.enviados: list[tuple[int, str]] = []

    async def responder(self, chat_id, texto) -> None:
        self.enviados.append((chat_id, texto))


class FakeEjecutar:
    def __init__(self, respuesta) -> None:
        self._r = respuesta
        self.llamadas: list[dict] = []

    async def __call__(self, **kw):
        self.llamadas.append(kw)
        return self._r


class FakeGobiernoStore:
    def __init__(self) -> None:
        self.budget: dict[tuple[int, str], int] = {}

    async def permitir_rate(self, tenant_id, usuario_id, limite, ventana_s):
        return True

    async def reservar_presupuesto(self, tenant_id, fecha, costo, limite, ttl_s):
        k = (tenant_id, fecha)
        usado = self.budget.get(k, 0)
        if usado + costo > limite:
            return False
        self.budget[k] = usado + costo
        return True


_SESSION = object()


def _ctx():
    return Contexto(tenant_id=1, usuario_id=42, rol="vendedor", origen="bot",
                    capacidades=frozenset({"bot_telegram"}))


def _update(texto="hola", chat_id=555):
    return UpdateBot(update_id=100, chat_id=chat_id, telegram_id=555, texto=texto)


def _handler(*, gobierno, ejecutar, dispatcher):
    return crear_turno_handler(
        dispatcher=dispatcher,
        memoria=lambda s: FakeMemoria(),
        costos=lambda s: FakeCostos(),
        crear_recursos=lambda s: object(),
        ejecutar=ejecutar,
        gobierno=gobierno,
    )


async def test_presupuesto_excedido_no_llama_al_modelo():
    # presupuesto 1 < costo 1000 → el 1er turno ya se corta, sin tocar el proveedor ni el bucle.
    gob = Gobierno(
        store=FakeGobiernoStore(),
        plataforma=PoliticaGobierno(presupuesto_diario=1, costo_estimado_turno=1000),
    )
    ejecutar = FakeEjecutar(RespuestaAgente(texto="no se usa", ruta="texto"))
    dispatcher = FakeDispatcher()
    notif = FakeNotificador()

    await _handler(gobierno=gob, ejecutar=ejecutar, dispatcher=dispatcher)(
        _update(), _ctx(), _SESSION, notif
    )

    assert ejecutar.llamadas == []          # el bucle del agente NUNCA corrió (0 llamadas al modelo)
    assert dispatcher.selecciones == []     # ni siquiera se resolvió el proveedor
    assert notif.enviados == [(555, MENSAJE_PRESUPUESTO)]


async def test_con_cupo_el_turno_corre_normal():
    gob = Gobierno(
        store=FakeGobiernoStore(),
        plataforma=PoliticaGobierno(presupuesto_diario=1_000_000, costo_estimado_turno=1000),
    )
    ejecutar = FakeEjecutar(RespuestaAgente(texto="Listo.", ruta="texto"))
    dispatcher = FakeDispatcher()
    notif = FakeNotificador()

    await _handler(gobierno=gob, ejecutar=ejecutar, dispatcher=dispatcher)(
        _update(), _ctx(), _SESSION, notif
    )

    assert len(ejecutar.llamadas) == 1      # el turno corrió el bucle del agente
    assert dispatcher.selecciones == [1]
    assert notif.enviados == [(555, "Listo.")]
