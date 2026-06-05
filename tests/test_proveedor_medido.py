"""Entregable 4 — `ProveedorMedido`: token accounting por el borde del proveedor (con fakes).

Pin del contrato:
  - cada `generate` lee `response.usage`, normaliza las claves (Claude `input_tokens`/`output_tokens`,
    OpenAI `prompt_tokens`/`completion_tokens`) y acumula en el `CostosStore` (fecha Colombia,
    modelo = el `model` de la llamada);
  - dos `generate` seguidos acumulan dos veces (las 2 generaciones del turno cuentan naturalmente);
  - best-effort: si el store lanza, `generate` igual devuelve la respuesta (no rompe el turno);
  - `usage` vacío/ausente → no se acumula (no escribe filas de ceros).
"""
from core.config.timezone import today_co
from core.llm.base import LLMResponse
from core.llm.medicion import ProveedorMedido


# --------------------------------- fakes ----------------------------------

class FakeLLM:
    nombre = "fake"
    api_key = "k"

    def __init__(self, respuestas: list[LLMResponse]):
        self._respuestas = list(respuestas)
        self.llamadas: list[dict] = []

    async def generate(self, **kw) -> LLMResponse:
        self.llamadas.append(kw)
        return self._respuestas.pop(0)


class FakeCostos:
    def __init__(self, *, falla: bool = False):
        self.llamadas: list[dict] = []
        self.falla = falla

    async def acumular(self, *, fecha, modelo, tokens_in, tokens_out):
        if self.falla:
            raise RuntimeError("fallo al acumular costos")
        self.llamadas.append(
            {"fecha": fecha, "modelo": modelo, "tokens_in": tokens_in, "tokens_out": tokens_out}
        )


def _resp(usage: dict) -> LLMResponse:
    return LLMResponse(text="ok", usage=usage)


# --------------------------------- tests ----------------------------------

async def test_preserva_nombre_y_api_key_del_envuelto():
    medido = ProveedorMedido(FakeLLM([]), FakeCostos())
    assert medido.nombre == "fake"
    assert medido.api_key == "k"


async def test_acumula_una_vez_por_generate_normalizando_claves():
    fake = FakeLLM([
        _resp({"input_tokens": 10, "output_tokens": 4}),      # forma Claude
        _resp({"prompt_tokens": 7, "completion_tokens": 3}),  # forma OpenAI
    ])
    costos = FakeCostos()
    medido = ProveedorMedido(fake, costos)

    r1 = await medido.generate(messages=[], tools=[], model="m1", system=None)
    r2 = await medido.generate(messages=[], tools=[], model="m2", system=None)

    assert (r1.text, r2.text) == ("ok", "ok")
    assert costos.llamadas == [
        {"fecha": today_co(), "modelo": "m1", "tokens_in": 10, "tokens_out": 4},
        {"fecha": today_co(), "modelo": "m2", "tokens_in": 7, "tokens_out": 3},
    ]


async def test_dos_generaciones_acumulan_dos_veces():
    fake = FakeLLM([
        _resp({"input_tokens": 5, "output_tokens": 2}),
        _resp({"input_tokens": 6, "output_tokens": 1}),
    ])
    costos = FakeCostos()
    medido = ProveedorMedido(fake, costos)

    await medido.generate(messages=[], tools=[], model="m", system=None)
    await medido.generate(messages=[], tools=[], model="m", system=None)

    assert len(costos.llamadas) == 2
    assert sum(c["tokens_in"] for c in costos.llamadas) == 11


async def test_store_que_falla_no_rompe_generate():
    fake = FakeLLM([_resp({"input_tokens": 9, "output_tokens": 9})])
    medido = ProveedorMedido(fake, FakeCostos(falla=True))

    resp = await medido.generate(messages=[], tools=[], model="m", system=None)

    assert resp.text == "ok"            # la respuesta del modelo sale pese al fallo del store


async def test_usage_vacio_no_acumula():
    fake = FakeLLM([_resp({})])
    costos = FakeCostos()
    medido = ProveedorMedido(fake, costos)

    await medido.generate(messages=[], tools=[], model="m", system=None)

    assert costos.llamadas == []        # sin usage, no se escribe nada
