"""Eval de function-call del bot RESTAURANTE (F7 / ADR 0032): corpus de pedidos con modificadores.

Plano CONTRATO (como el corpus POS): cada caso fija el `ToolCall` GOLD que el modelo debería emitir
para un mensaje real de restaurante y lo ejecuta contra las tools REALES (`ai.pedidos_tools`) con la
carta Siriuss sembrada. Se mide: herramienta+args correctos (el pedido queda con los ítems, los
modificadores y el TOTAL EXACTO del catálogo) y CERO alucinaciones de precio/producto (los casos
inválidos DEBEN volver como error recuperable con sugerencias — el bot pregunta, jamás inventa).

Meta del goal: ≥90% de acierto y 0 alucinaciones. El corpus es determinista (sin LLM ni claves).
"""
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai.envelope import Contexto, ErrorTool
from ai.pedidos_tools import PedidosDeps, ejecutar
from core.llm.base import ToolCall
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import PedidosService
from tests.carta_siriuss import sembrar_carta_siriuss

pytestmark = pytest.mark.eval

_PLATO = "plato fuerte del día"

# (mensaje del cliente, args GOLD de armar_pedido, subtotal esperado — SIEMPRE del catálogo).
# 16 casos VÁLIDOS: la tool registra exacto. El plato vale $19.000 (delta 0 en todas las opciones).
_VALIDOS = [
    ("un plato del día con carne asada, arroz y tajadas",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["carne asada", "arroz blanco o de coco", "tajadas"]}],
     "19000.00"),
    ("plato con cerdo asado y lentejas",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["cerdo asado", "lentejas"]}],
     "19000.00"),
    ("almuerzo con pollo frito, ensalada y tajadas",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["pollo frito", "ensalada de payaso", "tajadas"]}],
     "19000.00"),
    ("plato del día de pechuga asada con lentejas",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["pechuga asada", "lentejas"]}],
     "19000.00"),
    ("uno de albóndigas con arroz",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["albóndigas", "arroz blanco o de coco"]}],
     "19000.00"),
    ("plato con lengua en salsa, tajadas y lentejas",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["lengua en salsa", "tajadas", "lentejas"]}],
     "19000.00"),
    ("sobrebarriga criolla con arroz de coco",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["sobrebarriga criolla", "arroz blanco o de coco"]}],
     "19000.00"),
    ("salpicón de jurel con ensalada",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["salpicón de jurel", "ensalada de payaso"]}],
     "19000.00"),
    ("carne en bistec con tajadas",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["carne en bistec", "tajadas"]}],
     "19000.00"),
    ("cerdo en bistec con lentejas y arroz",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["cerdo en bistec", "lentejas", "arroz blanco o de coco"]}],
     "19000.00"),
    ("dos platos del día, ambos de carne asada con arroz",
     [{"producto": _PLATO, "cantidad": 2, "modificadores": ["carne asada", "arroz blanco o de coco"]}],
     "38000.00"),
    ("una sopa de hueso",
     [{"producto": "sopa de hueso", "cantidad": 1}],
     "14000.00"),
    ("dos sopas de hueso",
     [{"producto": "sopa de hueso", "cantidad": 2}],
     "28000.00"),
    ("el menú especial",
     [{"producto": "menú especial", "cantidad": 1}],
     "21000.00"),
    ("dos menús especiales",
     [{"producto": "menú especial", "cantidad": 2}],
     "42000.00"),
    ("un plato de pollo frito con tajadas y una sopa",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["pollo frito", "tajadas"]},
      {"producto": "sopa de hueso", "cantidad": 1}],
     "33000.00"),
]

# 4 casos INVÁLIDOS: el bot debe PREGUNTAR (error recuperable), jamás registrar ni inventar.
_INVALIDOS = [
    ("plato del día sin kriptonita",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["carne asada", "sin kriptonita"]}]),
    ("plato del día sin elegir proteína",
     [{"producto": _PLATO, "cantidad": 1, "modificadores": ["tajadas"]}]),
    ("plato con tres acompañantes",   # max 2 (D1 resuelta: incluye 2)
     [{"producto": _PLATO, "cantidad": 1,
       "modificadores": ["carne asada", "tajadas", "lentejas", "ensalada de payaso"]}]),
    ("una lasaña boloñesa",           # no existe en la carta
     [{"producto": "lasaña boloñesa", "cantidad": 1}]),
]


def _ctx() -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=1, rol="vendedor", cliente_telefono="573001112233",
        capacidades=frozenset({"pack_pedidos"}),
    )


@pytest.mark.parametrize("mensaje, items, subtotal", _VALIDOS, ids=[c[0][:40] for c in _VALIDOS])
async def test_pedido_valido_registra_exacto(tenant, mensaje, items, subtotal):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await sembrar_carta_siriuss(s)
        deps = PedidosDeps(pedidos=PedidosService(SqlPedidosRepository(s)))
        res = await ejecutar(
            ToolCall(id="t", name="armar_pedido", arguments={"items": items}), _ctx(), deps
        )
        assert not isinstance(res, ErrorTool), f"{mensaje!r}: {getattr(res, 'detail', None)}"
        # CERO alucinación de precio: el subtotal sale EXACTO del catálogo sembrado.
        assert res.data["subtotal"] == subtotal, mensaje
        assert len(res.data["items"]) == len(items)
        # Los modificadores del gold quedaron resueltos (snapshot con nombre canónico del catálogo).
        for gold, item in zip(items, res.data["items"]):
            assert len(item["modificadores"]) == len(gold.get("modificadores", [])), mensaje


@pytest.mark.parametrize("mensaje, items", _INVALIDOS, ids=[c[0][:40] for c in _INVALIDOS])
async def test_pedido_invalido_pregunta_no_inventa(tenant, mensaje, items):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await sembrar_carta_siriuss(s)
        deps = PedidosDeps(pedidos=PedidosService(SqlPedidosRepository(s)))
        res = await ejecutar(
            ToolCall(id="t", name="armar_pedido", arguments={"items": items}), _ctx(), deps
        )
        assert isinstance(res, ErrorTool) and res.recuperable, mensaje
        # No registró NADA: preguntar, no inventar.
        from sqlalchemy import text
        n = (await s.execute(text("SELECT count(*) FROM pedidos"))).scalar_one()
        assert n == 0, mensaje
