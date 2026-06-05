"""Entregable 2 — bucle del agente + respuesta NL híbrida (con proveedor y ejecutor falsos).

Pin de la política fijada en el checkpoint (B2):
  - éxito → resumen del envelope, sin 2ª generación;
  - Preguntar/Confirmar → directo al usuario, sin re-promptear;
  - ErrorTool recuperable → 2ª generación con el tool_result;
  - ErrorTool no recuperable → mensaje directo;
  - topes: máx 2 generaciones de modelo y 1 herramienta por turno;
  - se toma solo el primer tool_call; el catálogo expuesto llega al modelo.
"""
from ai.agent import SIN_RESPUESTA, RespuestaAgente, ejecutar_turno
from ai.envelope import Contexto, ErrorTool, Resultado
from ai.rieles import Confirmar, Preguntar
from core.llm.base import LLMResponse, Message, ToolCall, ToolSpec
from core.llm.factory import LLMResuelto


# --------------------------------- fakes ----------------------------------

class FakeLLM:
    """Proveedor que devuelve respuestas pre-cargadas y captura lo que recibió en cada llamada."""

    nombre = "fake"
    api_key = "k"

    def __init__(self, respuestas: list[LLMResponse]):
        self._respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    async def generate(self, *, messages, tools, model, system=None, **kw) -> LLMResponse:
        self.llamadas.append({"messages": list(messages), "tools": tools, "model": model, "system": system})
        return self._respuestas.pop(0)


class FakeEjecutor:
    def __init__(self, catalogo: list[ToolSpec], resultado):
        self._catalogo = catalogo
        self._resultado = resultado
        self.ejecutadas: list[ToolCall] = []

    def exponer_catalogo(self, ctx) -> list[ToolSpec]:
        return self._catalogo

    async def ejecutar(self, tool_call, ctx, recursos):
        self.ejecutadas.append(tool_call)
        return self._resultado


# --------------------------------- helpers --------------------------------

def _ctx() -> Contexto:
    return Contexto(tenant_id=1, usuario_id=2, rol="vendedor", capacidades=frozenset({"fiados"}))


def _spec(nombre="registrar_venta") -> ToolSpec:
    return ToolSpec(name=nombre, description="d", parameters={"type": "object"})


def _call(nombre="registrar_venta", id="c1", args=None) -> ToolCall:
    return ToolCall(id=id, name=nombre, arguments=args or {})


def _resp(text=None, tool_calls=()) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(tool_calls))


def _proveedor(fake: FakeLLM) -> LLMResuelto:
    return LLMResuelto(provider=fake, model="modelo-x", provider_nombre="fake")


async def _turno(fake: FakeLLM, ejecutor: FakeEjecutor, texto="2 martillo") -> RespuestaAgente:
    return await ejecutar_turno(
        texto=texto, ctx=_ctx(), ejecutor=ejecutor, recursos=object(), proveedor=_proveedor(fake)
    )


# ------------------------------- tests ------------------------------------

async def test_modelo_responde_texto_sin_herramienta():
    fake = FakeLLM([_resp(text="Claro, ¿en qué te ayudo?")])
    ejecutor = FakeEjecutor([_spec()], resultado=None)

    res = await _turno(fake, ejecutor, texto="hola")

    assert res.ruta == "texto"
    assert res.texto == "Claro, ¿en qué te ayudo?"
    assert res.generaciones == 1
    assert ejecutor.ejecutadas == []          # no se ejecutó ninguna herramienta


async def test_exito_usa_resumen_sin_segunda_generacion():
    fake = FakeLLM([_resp(tool_calls=[_call()])])
    resultado = Resultado(
        data={"venta_id": 1}, resumen="Venta #1 por $23.800 registrada.",
        evento="venta_registrada", idempotente="aplicada",
    )
    ejecutor = FakeEjecutor([_spec()], resultado=resultado)

    res = await _turno(fake, ejecutor)

    assert res.ruta == "tool"
    assert res.texto == "Venta #1 por $23.800 registrada."
    assert res.evento == "venta_registrada"
    assert res.idempotente == "aplicada"
    assert res.generaciones == 1              # cero llamada extra al modelo
    assert len(fake.llamadas) == 1
    assert res.tool == "registrar_venta"


async def test_preguntar_va_directo_sin_repromptear():
    fake = FakeLLM([_resp(tool_calls=[_call()])])
    pregunta = Preguntar("producto_no_encontrado", "No encontré ningún producto para «taladro». ¿Cuál es?")
    ejecutor = FakeEjecutor([_spec()], resultado=pregunta)

    res = await _turno(fake, ejecutor)

    assert res.ruta == "riel"
    assert res.texto == pregunta.mensaje
    assert res.generaciones == 1              # NO se re-promptea al modelo
    assert len(fake.llamadas) == 1


async def test_producto_ambiguo_relaya_la_lista_de_candidatos():
    fake = FakeLLM([_resp(tool_calls=[_call()])])
    pregunta = Preguntar(
        "producto_ambiguo",
        "Hay varios que coinciden con «cemento»: Cemento gris 50kg, Cemento blanco 25kg. ¿Cuál?",
    )
    ejecutor = FakeEjecutor([_spec()], resultado=pregunta)

    res = await _turno(fake, ejecutor)

    assert res.ruta == "riel"
    assert "Cemento gris 50kg" in res.texto and "Cemento blanco 25kg" in res.texto
    assert res.generaciones == 1


async def test_confirmar_va_directo_sin_repromptear():
    fake = FakeLLM([_resp(tool_calls=[_call("registrar_gasto")])])
    confirmar = Confirmar("Registrar gasto de $15.000 en transporte. ¿Confirmo?")
    ejecutor = FakeEjecutor([_spec("registrar_gasto")], resultado=confirmar)

    res = await _turno(fake, ejecutor)

    assert res.ruta == "riel"
    assert res.texto == confirmar.resumen
    assert res.generaciones == 1
    assert len(fake.llamadas) == 1


# --- CR-2: confirmacion_pendiente lleva el ToolCall SOLO en la rama Confirmar ---

async def test_confirmar_puebla_confirmacion_pendiente():
    call = _call("registrar_gasto")
    fake = FakeLLM([_resp(tool_calls=[call])])
    confirmar = Confirmar("Registrar gasto de $15.000 en transporte. ¿Confirmo?")
    ejecutor = FakeEjecutor([_spec("registrar_gasto")], resultado=confirmar)

    res = await _turno(fake, ejecutor)

    assert res.confirmacion_pendiente == call      # el handler lo guarda para el re-despacho


async def test_resultado_no_puebla_confirmacion_pendiente():
    fake = FakeLLM([_resp(tool_calls=[_call()])])
    resultado = Resultado(data={}, resumen="Venta registrada.", evento="venta_registrada")
    ejecutor = FakeEjecutor([_spec()], resultado=resultado)

    res = await _turno(fake, ejecutor)

    assert res.confirmacion_pendiente is None


async def test_preguntar_no_puebla_confirmacion_pendiente():
    fake = FakeLLM([_resp(tool_calls=[_call()])])
    pregunta = Preguntar("producto_no_encontrado", "¿Cuál producto?")
    ejecutor = FakeEjecutor([_spec()], resultado=pregunta)

    res = await _turno(fake, ejecutor)

    assert res.confirmacion_pendiente is None


async def test_error_recuperable_repromptea_con_tool_result():
    fake = FakeLLM([
        _resp(tool_calls=[_call(id="c1")]),
        _resp(text="Solo quedan 3 de martillo. ¿Registro 3?"),
    ])
    error = ErrorTool("stock_insuficiente", "Quedan 3 de 'martillo', se pidieron 5.", recuperable=True)
    ejecutor = FakeEjecutor([_spec()], resultado=error)

    res = await _turno(fake, ejecutor, texto="5 martillo")

    assert res.ruta == "texto"
    assert res.texto == "Solo quedan 3 de martillo. ¿Registro 3?"
    assert res.generaciones == 2              # se re-prompteó
    assert len(fake.llamadas) == 2
    assert len(ejecutor.ejecutadas) == 1      # una sola ejecución de herramienta
    # 2ª generación con la tripleta bien formada y agnóstica: user → assistant(tool_call) → tool
    msgs2 = fake.llamadas[1]["messages"]
    assert [m.role for m in msgs2] == ["user", "assistant", "tool"]
    asistente = msgs2[1]
    assert asistente.tool_calls and asistente.tool_calls[0].id == "c1"
    tool_result = msgs2[2]
    assert tool_result.tool_call_id == "c1"
    assert tool_result.name == "registrar_venta"
    assert "stock_insuficiente" in tool_result.content   # el envelope del error viaja al modelo


async def test_error_no_recuperable_no_repromptea():
    fake = FakeLLM([_resp(tool_calls=[_call("registrar_compra")])])
    error = ErrorTool("permiso_denegado", "registrar_compra requiere rol admin", recuperable=False)
    ejecutor = FakeEjecutor([_spec("registrar_compra")], resultado=error)

    res = await _turno(fake, ejecutor)

    assert res.ruta == "error"
    assert res.generaciones == 1              # sin 2ª generación
    assert len(fake.llamadas) == 1
    assert res.texto                           # mensaje no vacío para el usuario


async def test_tope_no_ejecuta_un_segundo_tool():
    # ronda 1: tool → error recuperable; ronda 2: el modelo vuelve a pedir tool → NO se ejecuta.
    fake = FakeLLM([
        _resp(tool_calls=[_call(id="c1")]),
        _resp(text="", tool_calls=[_call(id="c2")]),
    ])
    error = ErrorTool("stock_insuficiente", "Quedan 3", recuperable=True)
    ejecutor = FakeEjecutor([_spec()], resultado=error)

    res = await _turno(fake, ejecutor)

    assert len(ejecutor.ejecutadas) == 1      # tope: 1 herramienta mutante por turno
    assert res.generaciones == 2
    assert res.ruta == "texto"
    assert res.texto == SIN_RESPUESTA          # 2ª generación sin texto → respaldo


async def test_solo_se_ejecuta_el_primer_tool_call():
    fake = FakeLLM([_resp(tool_calls=[_call(id="c1"), _call("registrar_gasto", id="c2")])])
    resultado = Resultado(data={}, resumen="ok", evento="venta_registrada")
    ejecutor = FakeEjecutor([_spec()], resultado=resultado)

    await _turno(fake, ejecutor)

    assert len(ejecutor.ejecutadas) == 1
    assert ejecutor.ejecutadas[0].id == "c1"  # tope duro: solo el primero


async def test_el_catalogo_expuesto_llega_al_modelo():
    catalogo = [_spec("registrar_venta"), _spec("registrar_gasto")]
    fake = FakeLLM([_resp(text="hola")])
    ejecutor = FakeEjecutor(catalogo, resultado=None)

    await _turno(fake, ejecutor, texto="hola")

    assert fake.llamadas[0]["tools"] == catalogo
    # el texto del usuario entra como mensaje del turno
    assert any(isinstance(m, Message) and m.role == "user" and m.content == "hola"
               for m in fake.llamadas[0]["messages"])
