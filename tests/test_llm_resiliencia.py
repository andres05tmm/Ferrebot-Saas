"""Fase 0 (ADR 0023) — resiliencia de la capa LLM: retry con backoff+jitter y fallback de proveedor.

Invariante (TDD test-primero): el reintento vive en el BORDE del proveedor (`generate`), nunca en el
bucle del agente — un reintento de la llamada al modelo JAMÁS re-ejecuta herramientas ya despachadas.

Lo demás (backoff, jitter, clasificación transitorio/permanente, fallback una sola vez) se fija aquí
con fakes: el decorador `ProveedorResiliente` espeja el patrón de `ProveedorMedido`.
"""
import pytest

from apps.wa.agent import correr_bucle
from ai.envelope import Contexto, Resultado
from core.llm.base import LLMPermanente, LLMResponse, LLMTransitorio, ToolCall
from core.llm.factory import LLMResuelto
from core.llm.resiliencia import ProveedorResiliente


class _Flaky:
    """Proveedor fake: lanza las excepciones programadas y luego devuelve respuestas en orden."""

    nombre = "fake"
    api_key = "x"

    def __init__(self, guion: list) -> None:
        self._guion = list(guion)      # cada item: Exception a lanzar o LLMResponse a devolver
        self.llamadas: list[dict] = []

    async def generate(self, **kwargs) -> LLMResponse:
        self.llamadas.append(kwargs)
        paso = self._guion.pop(0)
        if isinstance(paso, Exception):
            raise paso
        return paso


class _Sleeper:
    def __init__(self): self.esperas: list[float] = []
    async def __call__(self, s: float) -> None: self.esperas.append(s)


_RESP = LLMResponse(text="ok", tool_calls=[])


def _resiliente(provider, **kw) -> ProveedorResiliente:
    kw.setdefault("sleep", _Sleeper())
    kw.setdefault("rng", lambda: 0.5)          # jitter determinista en tests
    return ProveedorResiliente(provider, **kw)


# --- reintentos ---------------------------------------------------------------
async def test_reintenta_transitorio_y_devuelve_la_respuesta():
    provider = _Flaky([LLMTransitorio("429"), LLMTransitorio("timeout"), _RESP])
    sleeper = _Sleeper()
    resp = await _resiliente(provider, sleep=sleeper).generate(model="m", messages=[], tools=[])
    assert resp.text == "ok"
    assert len(provider.llamadas) == 3
    assert len(sleeper.esperas) == 2
    assert sleeper.esperas[0] < sleeper.esperas[1]      # backoff creciente


async def test_transitorio_agotado_propaga():
    provider = _Flaky([LLMTransitorio("429")] * 3)
    with pytest.raises(LLMTransitorio):
        await _resiliente(provider, intentos=3).generate(model="m", messages=[], tools=[])
    assert len(provider.llamadas) == 3


async def test_permanente_no_se_reintenta():
    provider = _Flaky([LLMPermanente("400 bad request")])
    with pytest.raises(LLMPermanente):
        await _resiliente(provider).generate(model="m", messages=[], tools=[])
    assert len(provider.llamadas) == 1


async def test_excepcion_desconocida_no_se_reintenta():
    provider = _Flaky([RuntimeError("bug propio")])
    with pytest.raises(RuntimeError):
        await _resiliente(provider).generate(model="m", messages=[], tools=[])
    assert len(provider.llamadas) == 1


# --- fallback de proveedor ------------------------------------------------------
async def test_fallback_una_vez_al_agotar_el_primario_con_su_modelo():
    primario = _Flaky([LLMTransitorio("500")] * 3)
    respaldo = _Flaky([_RESP])
    resp = await _resiliente(
        primario, respaldo=respaldo, modelo_respaldo="modelo-b", intentos=3
    ).generate(model="modelo-a", messages=[], tools=[])
    assert resp.text == "ok"
    assert len(respaldo.llamadas) == 1
    assert respaldo.llamadas[0]["model"] == "modelo-b"   # el respaldo usa SU modelo


async def test_fallback_no_aplica_en_permanente():
    primario = _Flaky([LLMPermanente("400")])
    respaldo = _Flaky([_RESP])
    with pytest.raises(LLMPermanente):
        await _resiliente(primario, respaldo=respaldo).generate(model="m", messages=[], tools=[])
    assert respaldo.llamadas == []


# --- invariante: el retry NO re-ejecuta herramientas ya despachadas -------------
async def test_reintento_no_reejecuta_tools_ya_despachadas():
    tc = ToolCall(id="c1", name="listar_servicios", arguments={})
    provider = _Flaky([
        LLMResponse(text=None, tool_calls=[tc]),   # 1ª generación: pide la herramienta
        LLMTransitorio("blip"),                    # 2ª generación: falla transitoria…
        LLMResponse(text="listo", tool_calls=[]),  # …y su reintento responde
    ])
    ejecuciones = []

    async def fake_ejecutar(call, ctx, deps):
        ejecuciones.append(call.name)
        return Resultado(data={}, resumen="ok")

    ctx = Contexto(tenant_id=1, usuario_id=0, rol="cliente", origen="whatsapp",
                   cliente_telefono="573001112233", capacidades=frozenset())
    resuelto = LLMResuelto(provider=_resiliente(provider), model="m", provider_nombre="fake")
    texto = await correr_bucle(
        proveedor=resuelto, system="s", tools=[], ctx=ctx, deps=None,
        historial=[], texto="hola", ejecutar=fake_ejecutar,
    )
    assert texto == "listo"
    assert ejecuciones == ["listar_servicios"]     # UNA sola ejecución pese al reintento
