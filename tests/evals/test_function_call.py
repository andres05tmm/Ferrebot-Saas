"""Eval — precisión de la llamada a herramienta (function-call accuracy).

Corre el corpus de `corpus.py` contra el runtime REAL del agente (bypass + despachador), sin BD ni
LLM (en CI las claves de proveedor van vacías). Tres planos:

  - PARSEO    → `ai.bypass.analizar`: el texto se vuelve intención de venta o cae al modelo con un
                motivo estable (typos de cantidad, plurales, fracciones, gates de desactivación).
  - DESPACHO  → `ai.bypass.Bypass.intentar`: el camino rápido emite `registrar_venta` con los
                `items` exactos, o defiere al modelo (typo de producto, fracción inexistente, consulta,
                cliente). El escalonado/mayorista por umbral lo resuelve el motor y ya no se difiere. Es
                el gate del ~60 % de ventas sin IA.
  - CONTRATO  → `ai.dispatcher.Dispatcher.ejecutar`: un `ToolCall` "gold" (el que el modelo debería
                emitir para gasto/fiado con montos coloquiales) valida, gatea por capacidad/confirmación
                y ejecuta. El mapeo lenguaje-natural→args del LLM vivo se evalúa aparte (con claves).

Una regresión en cualquiera de estos planos rompe el corazón del producto: que el agente llame a la
herramienta correcta con los argumentos correctos.
"""
from __future__ import annotations

import pytest

from ai.bypass import CaeAlModelo, VentaSimple, analizar
from ai.envelope import ErrorTool, Resultado
from ai.rieles import Confirmar
from core.llm.base import ToolCall
from tests.evals._harness import construir, ctx_eval
from tests.evals.corpus import CONTRATO, DESPACHO, PARSEO

pytestmark = pytest.mark.eval


# --- 1) PARSEO: texto → intención (función pura, sin catálogo) ----------------
@pytest.mark.parametrize("caso", PARSEO, ids=lambda c: c.frase.strip() or "vacio")
def test_parseo_intencion(caso):
    res = analizar(caso.frase)
    if caso.es_venta:
        assert isinstance(res, VentaSimple), f"esperaba venta, no {res}"
        assert res.producto == caso.producto
        assert res.componentes == caso.componentes
    else:
        assert isinstance(res, CaeAlModelo), f"esperaba caer al modelo, no {res}"
        assert res.motivo == caso.motivo


# --- 2) DESPACHO: frase → ToolCall registrar_venta con args exactos -----------
@pytest.mark.parametrize("caso", DESPACHO, ids=lambda c: c.frase)
async def test_despacho_llamada(caso):
    h = construir()
    res = await h.bypass.intentar(caso.frase, ctx_eval(), h.recursos)

    if caso.es_venta:
        assert isinstance(res, Resultado), f"esperaba ejecución de venta, no {res}"
        header = h.ventas_repo.ultimo_header
        emitidos = tuple((l.producto_id, l.cantidad) for l in header.lineas)
        assert emitidos == caso.items, f"args de la llamada: {emitidos} != {caso.items}"
        assert h.ventas_repo.creadas == 1
    else:
        # El bypass no adivina: defiere al modelo y NO ejecuta nada.
        assert res is None, f"debía deferir al modelo, devolvió {res}"
        assert h.ventas_repo.creadas == 0


# --- 3) CONTRATO: ToolCall gold del modelo → despachador ----------------------
@pytest.mark.parametrize("caso", CONTRATO, ids=lambda c: c.descripcion)
async def test_contrato_despachador(caso):
    h = construir()
    ctx = ctx_eval(
        key=f"contrato-{caso.tool}", confirmado=caso.confirmado, capacidades=caso.capacidades
    )
    tool_call = ToolCall(id="gold", name=caso.tool, arguments=caso.args)

    res = await h.dispatcher.ejecutar(tool_call, ctx, h.recursos)

    if caso.espera == "resultado":
        assert isinstance(res, Resultado), f"esperaba Resultado, no {res}"
        if caso.evento is not None:
            assert res.evento == caso.evento
    elif caso.espera == "confirmar":
        assert isinstance(res, Confirmar), f"esperaba Confirmar (riel R3), no {res}"
    elif caso.espera == "error":
        assert isinstance(res, ErrorTool), f"esperaba ErrorTool, no {res}"
        assert res.error == caso.codigo_error
    else:  # pragma: no cover - guardia de corpus mal formado
        pytest.fail(f"espera desconocida: {caso.espera}")
