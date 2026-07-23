"""IA de restaurante — F7 / ADR 0032: upsell acotado, resumen del día e ingeniería de menú.

Riel del upsell (test-primero, anti-alucinación): el bot solo sugiere el complemento configurado si
existe en el catálogo REAL (con su precio del catálogo); un nombre que no resuelve NO se sugiere.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import ai.pedidos_tools as tools_mod
from ai.envelope import Contexto, ErrorTool
from ai.pedidos_tools import PedidosDeps, ejecutar
from core.config.timezone import now_co
from core.llm.base import ToolCall
from modules.pedidos.conversion import convertir_pedido
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido, PedidosService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService
from tests.carta_siriuss import sembrar_carta_siriuss


def _ctx() -> Contexto:
    return Contexto(
        tenant_id=1, usuario_id=1, rol="vendedor", cliente_telefono="573001112233",
        capacidades=frozenset({"pack_pedidos"}),
    )


async def test_upsell_solo_sugiere_productos_reales(tenant, monkeypatch):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await sembrar_carta_siriuss(s)
        deps = PedidosDeps(pedidos=PedidosService(SqlPedidosRepository(s)))
        args = {"items": [{"producto": "sopa de hueso", "cantidad": 1}]}

        # Configurado un producto REAL → la coletilla sale con el precio DEL CATÁLOGO.
        async def _upsell_real(tenant_id: int) -> str | None:
            return "menú especial"

        monkeypatch.setattr(tools_mod, "_leer_upsell", _upsell_real)
        res = await ejecutar(ToolCall(id="t", name="armar_pedido", arguments=args), _ctx(), deps)
        assert not isinstance(res, ErrorTool)
        assert "Menú especial" in res.resumen and "$21.000" in res.resumen

        # Configurado un producto INEXISTENTE → NO se sugiere nada (jamás inventar).
        async def _upsell_falso(tenant_id: int) -> str | None:
            return "brownie mágico"

        monkeypatch.setattr(tools_mod, "_leer_upsell", _upsell_falso)
        res2 = await ejecutar(
            ToolCall(id="t2", name="armar_pedido", arguments=args), _ctx(), deps
        )
        assert not isinstance(res2, ErrorTool)
        assert "brownie" not in res2.resumen.lower()


async def test_resumen_dia_e_ingenieria_de_menu(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await sembrar_carta_siriuss(s)
        repo = SqlPedidosRepository(s)
        svc = PedidosService(repo)
        # Un pedido de WhatsApp convertido (2 platos) + uno solo confirmado (1 sopa).
        await svc.armar_pedido(
            "573001112233",
            [ItemPedido(producto="plato fuerte del día", cantidad=Decimal("2"),
                        modificadores=("carne asada", "tajadas"))],
            ahora=now_co(),
        )
        pedido, _ = await svc.confirmar_pedido(
            "573001112233", direccion="Cl 1 # 2-3", metodo_pago="efectivo"
        )
        await s.commit()
        await convertir_pedido(
            pedido.id, repo=repo, ventas=VentaService(SqlVentasRepository(s)),
            usuario_id=ids["usuario"],
        )
        await s.commit()
        await svc.armar_pedido(
            "573009998877", [ItemPedido(producto="sopa de hueso", cantidad=Decimal("1"))],
            ahora=now_co(),
        )
        await svc.confirmar_pedido("573009998877", direccion="Cl 9", metodo_pago="efectivo")
        await s.commit()

        resumen = await repo.resumen_dia()
        canal_wa = next(c for c in resumen["canales"] if c["origen"] == "whatsapp")
        assert canal_wa["pedidos"] == 2 and canal_wa["vendidos"] == 1
        assert Decimal(canal_wa["vendido"]) == Decimal("41000.00")   # 2×19000 + 3000 domicilio
        assert resumen["top_platos"][0]["nombre"] == "Plato fuerte del día"
        assert resumen["ciclo_medio_min"] is not None and resumen["ciclo_medio_min"] >= 0

        # Ingeniería de menú: margen del plato = 19000 − (0.2×2000 + 0.25×9000) = 16350; rotación 2.
        ing = await repo.ingenieria_menu(dias=30)
        plato = next(f for f in ing if f["nombre"] == "Plato fuerte del día")
        assert plato["costo_plato"] == Decimal("2650.00")
        assert plato["margen"] == Decimal("16350.00")
        assert plato["rotacion"] == Decimal("2.000")
        assert plato["margen_total"] == Decimal("32700.000")
