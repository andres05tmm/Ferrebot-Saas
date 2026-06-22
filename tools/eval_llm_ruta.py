#!/usr/bin/env python3
"""Runner de la RUTA LLM: corre el loop del agente (ai.agent.ejecutar_turno) con el PROVEEDOR REAL
(Claude, clave de plataforma del .env) contra el catálogo REAL del tenant (DB `pr_scratch`), y mide
qué herramienta elige el modelo y con qué args. Complementa el replay del bypass (determinista).

HACE LLAMADAS DE API REALES (costo). No se ejecuta en CI; es una medición manual de la ruta del
modelo (multiproducto, funciones no-venta, typos vía consultar_producto). Las escrituras van a la DB
scratch y se ROLLBACK por caso (no ensucia).

Uso:  EVAL_LLM_DB_URL=postgresql+asyncpg://user:pass@host:5433/pr_scratch python -m tools.eval_llm_ruta
      (requiere una DB de tenant sembrada con el catálogo real + ANTHROPIC_API_KEY en .env)
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from ai.agent import ejecutar_turno
from ai.dispatcher import Dispatcher, Recursos
from ai.envelope import Contexto
from ai.ports import CatalogoDesdeVentas, Umbrales
from ai.tools import Deps
from ai.turno import construir_system_prompt
from core.config.settings import get_settings
from core.llm.factory import PlataformaLLM, Turno, get_llm
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.clientes.repository import SqlClientesRepository
from modules.clientes.service import ClientesService
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService

# URL de la DB del tenant sembrada (NO hardcodear credenciales): se toma del entorno.
URL = os.environ.get("EVAL_LLM_DB_URL", "")


class _UmbralesDefault:
    async def cargar(self, empresa_id):
        return Umbrales()      # defaults seguros (confirmar_mutaciones=True, tolerancias 1%/$1)


class _NoConfig:
    async def overrides(self, empresa_id):
        return {}


class _NoKey:
    async def api_key(self, empresa_id, provider):
        return None            # → get_llm cae al key de plataforma (.env)


class _SpyDispatcher:
    """Envuelve el Dispatcher: registra el ToolCall que el modelo pidió y delega la ejecución real."""

    def __init__(self, real: Dispatcher):
        self._real = real
        self.ultimo_call = None

    def exponer_catalogo(self, ctx):
        return self._real.exponer_catalogo(ctx)

    async def ejecutar(self, tool_call, ctx, recursos):
        self.ultimo_call = tool_call
        return await self._real.ejecutar(tool_call, ctx, recursos)


# (frase, herramienta_esperada, nota). herramienta_esperada None = texto/aclaración aceptable.
CASOS = [
    ("gasto 20000 almuerzo", "registrar_gasto", "gasto coloquial"),
    ("anota un gasto de 15 mil en transporte", "registrar_gasto", "monto '15 mil'"),
    ("cuanto vale el thinner", "consultar_producto", "consulta de precio"),
    ("cuanto vale el galon de vinilo davinci t1 blanco", "consultar_producto", "consulta con unidad"),
    ("cuanto vendi hoy", "consultar_ventas_dia", "reporte del día"),
    ("2 tiner", "consultar_producto", "typo → el modelo debe buscar thinner"),
    ("1 lija", "consultar_producto", "ambiguo → buscar/preguntar, no adivinar"),
    ("3 tornillo drywall 6x1 y 2 chazo 1/4", "consultar_producto", "multiproducto → empieza por consultar"),
    ("2000 de puntilla 1 sin cabeza", "consultar_producto", "modo pesos granel"),
    ("vinilo davinci t1 blanco cuanto cuesta el cuñete", "consultar_producto", "consulta variante"),
]


async def correr_caso(frase, proveedor, eng):
    async with AsyncSession(eng) as s:
        deps = Deps(
            ventas=VentaService(SqlVentasRepository(s)),
            caja=CajaService(SqlCajaRepository(s)),
            fiados=FiadosService(SqlFiadosRepository(s)),
            clientes=ClientesService(SqlClientesRepository(s)),
        )
        recursos = Recursos(
            deps=deps,
            catalogo=CatalogoDesdeVentas(SqlVentasRepository(s)),
            umbrales=_UmbralesDefault(),
        )
        disp = Dispatcher(config_store=_NoConfig(), key_store=_NoKey(), plataforma=None)
        spy = _SpyDispatcher(disp)
        ctx = Contexto(
            tenant_id=1, usuario_id=1, rol="vendedor", origen="bot",
            idempotency_key=f"llm-{abs(hash(frase)) % 10**8}", capacidades=frozenset({"fiados"}),
        )
        system = construir_system_prompt({})
        ruta = "?"
        try:
            resp = await ejecutar_turno(
                texto=frase, ctx=ctx, ejecutor=spy, recursos=recursos,
                proveedor=proveedor, system=system,
            )
            ruta = resp.ruta
        except Exception as e:
            ruta = f"EXC:{type(e).__name__}:{str(e)[:60]}"
        finally:
            await s.rollback()           # no persistir nada (la tool ya quedó capturada en el spy)
        tool = spy.ultimo_call.name if spy.ultimo_call else None
        args = spy.ultimo_call.arguments if spy.ultimo_call else None
        return (ruta, tool, args)


async def main():
    if not URL:
        raise SystemExit("Definí EVAL_LLM_DB_URL (DB de tenant sembrada). Ver el docstring.")
    settings = get_settings()
    plataforma = PlataformaLLM.desde_settings(settings)
    proveedor = await get_llm(
        1, turno=Turno.WORKER, config_store=_NoConfig(), key_store=_NoKey(), plataforma=plataforma,
    )
    eng = create_async_engine(URL)
    print(f"== RUTA LLM · proveedor={proveedor.provider_nombre} modelo={proveedor.model} ==\n")
    ok = 0
    for frase, esperada, nota in CASOS:
        ruta, tool, args = await correr_caso(frase, proveedor, eng)
        acierto = (tool == esperada) or (esperada is None and tool is None)
        ok += acierto
        marca = "OK " if acierto else "XX "
        args_s = "" if args is None else str({k: v for k, v in (args or {}).items() if k != "items"})
        items = (args or {}).get("items") if args else None
        print(f"{marca}{frase!r}")
        print(f"     esperaba={esperada} | modelo→ ruta={ruta} tool={tool} {args_s}")
        if items:
            print(f"     items={items}")
        print(f"     ({nota})")
    await eng.dispose()
    print(f"\n== tool-selection: {ok}/{len(CASOS)} ==")


if __name__ == "__main__":
    asyncio.run(main())
