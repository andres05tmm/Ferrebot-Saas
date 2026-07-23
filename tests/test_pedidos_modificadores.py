"""Modificadores de menú (F2 Pack Restaurante, ADR 0032 D3).

Invariantes (test-primero): el total es DETERMINISTA (precio catálogo + Σ delta_precio, verificado
contra el catálogo sembrado), y ANTI-ALUCINACIÓN: un modificador inexistente ("sin kriptonita") no
registra nada — el motor levanta error con sugerencias y el bot pregunta. El snapshot completo
(grupo, opción, delta) queda en el ítem; la conversión F1 lo conserva en la descripción de la venta.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.pedidos.errors import ModificadorInvalido, ModificadorNoEncontrado
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido, PedidosService


async def _seed_menu(s: AsyncSession) -> dict:
    """Hamburguesa $18.000 (grupo Personalización: sin cebolla Δ0, adición de queso Δ3.000;
    grupo Proteína obligatorio min1 max1: carne Δ0, pollo Δ0) + Limonada $5.000 sin grupos."""
    # Cocina 24h: el test no depende de la hora local a la que corra la suite.
    await s.execute(
        text(
            "INSERT INTO pedido_config (activo, hora_apertura, hora_cierre, minimo_pedido, "
            "tiempo_estimado_min, costo_domicilio_default) VALUES (true, '00:00', '23:59', 0, 45, 0)"
        )
    )
    ids = {}
    for nombre, precio in (("Hamburguesa", 18000), ("Limonada", 5000)):
        ids[nombre] = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, "
                    "permite_fraccion, activo) VALUES (:n, 'unidad', :p, 0, false, true) RETURNING id"
                ),
                {"n": nombre, "p": precio},
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, 50, 0)"),
            {"p": ids[nombre]},
        )
    async def _grupo(producto_id, nombre, min_sel, max_sel, obligatorio):
        return (
            await s.execute(
                text(
                    "INSERT INTO modificador_grupos (producto_id, nombre, min_sel, max_sel, "
                    "obligatorio, orden, activo) VALUES (:p, :n, :mn, :mx, :ob, 0, true) RETURNING id"
                ),
                {"p": producto_id, "n": nombre, "mn": min_sel, "mx": max_sel, "ob": obligatorio},
            )
        ).scalar_one()

    perso = await _grupo(ids["Hamburguesa"], "Personalización", 0, None, False)
    prote = await _grupo(ids["Hamburguesa"], "Proteína", 1, 1, True)
    for grupo, nombre, delta in (
        (perso, "Sin cebolla", 0), (perso, "Adición de queso", 3000),
        (prote, "Carne", 0), (prote, "Pollo", 0),
    ):
        await s.execute(
            text(
                "INSERT INTO modificador_opciones (grupo_id, nombre, delta_precio, activo) "
                "VALUES (:g, :n, :d, true)"
            ),
            {"g": grupo, "n": nombre, "d": delta},
        )
    await s.commit()
    return ids


def _svc(s: AsyncSession) -> PedidosService:
    return PedidosService(SqlPedidosRepository(s))


async def test_e2e_conversacional_dos_hamburguesas_una_sin_cebolla_y_limonada(tenant):
    # "2 hamburguesas, una sin cebolla y con adición de queso, y una limonada" → 3 ítems,
    # modificadores correctos y TOTAL EXACTO contra el catálogo.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_menu(s)
        res = await _svc(s).armar_pedido(
            "3001112233",
            [
                ItemPedido(producto="hamburguesa", cantidad=Decimal("1"), modificadores=("carne",)),
                ItemPedido(
                    producto="hamburguesa", cantidad=Decimal("1"),
                    modificadores=("carne", "sin cebolla", "adición de queso"),
                ),
                ItemPedido(producto="limonada", cantidad=Decimal("1")),
            ],
            ahora=now_co(),
        )
        await s.commit()

        pedido = res.pedido
        assert len(pedido.items) == 3
        # 18000 + (18000 + 0 + 0 + 3000) + 5000 = 44000
        assert pedido.subtotal == Decimal("44000.00")
        con_mods = pedido.items[1]
        assert con_mods.precio_unitario == Decimal("21000.00")
        nombres = [m["opcion"] for m in con_mods.modificadores]
        assert nombres == ["Carne", "Sin cebolla", "Adición de queso"]
        assert {m["grupo"] for m in con_mods.modificadores} == {"Proteína", "Personalización"}
        assert con_mods.modificadores[2]["delta_precio"] == "3000.00"
        assert pedido.items[2].modificadores in (None, [])


async def test_modificador_inexistente_no_registra_y_sugiere(tenant):
    # ANTI-ALUCINACIÓN: "sin kriptonita" → error con sugerencias del catálogo, sin pedido creado.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_menu(s)
        with pytest.raises(ModificadorNoEncontrado) as exc:
            await _svc(s).armar_pedido(
                "3001112233",
                [ItemPedido(producto="hamburguesa", cantidad=Decimal("1"),
                            modificadores=("carne", "sin kriptonita"))],
                ahora=now_co(),
            )
        assert exc.value.nombre == "sin kriptonita"
        assert "Sin cebolla" in exc.value.sugerencias
        await s.rollback()
        n = (await s.execute(text("SELECT count(*) FROM pedidos"))).scalar_one()
        assert n == 0


async def test_grupo_obligatorio_y_tope_maximo(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_menu(s)
        svc = _svc(s)
        # Sin proteína (grupo obligatorio min1) → el bot debe preguntar, no registrar.
        with pytest.raises(ModificadorInvalido):
            await svc.armar_pedido(
                "3001112233",
                [ItemPedido(producto="hamburguesa", cantidad=Decimal("1"))],
                ahora=now_co(),
            )
        # Dos proteínas (max1) → tampoco.
        with pytest.raises(ModificadorInvalido):
            await svc.armar_pedido(
                "3001112233",
                [ItemPedido(producto="hamburguesa", cantidad=Decimal("1"),
                            modificadores=("carne", "pollo"))],
                ahora=now_co(),
            )
        # La limonada no tiene grupos: pasa sin modificadores.
        res = await svc.armar_pedido(
            "3001112233", [ItemPedido(producto="limonada", cantidad=Decimal("2"))], ahora=now_co()
        )
        assert res.pedido.subtotal == Decimal("10000.00")


async def test_conversion_conserva_modificadores_en_descripcion(tenant):
    from modules.pedidos.conversion import convertir_pedido
    from modules.ventas.repository import SqlVentasRepository
    from modules.ventas.service import VentaService

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_menu(s)
        usuario_id = (
            await s.execute(
                text("INSERT INTO usuarios (nombre, rol) VALUES ('Caja','vendedor') RETURNING id")
            )
        ).scalar_one()
        repo = SqlPedidosRepository(s)
        svc = PedidosService(repo)
        res = await svc.armar_pedido(
            "3001112233",
            [ItemPedido(producto="hamburguesa", cantidad=Decimal("1"),
                        modificadores=("carne", "adición de queso"))],
            ahora=now_co(),
        )
        pedido, _ = await svc.confirmar_pedido(
            "3001112233", direccion="Cra 1 # 2-3", metodo_pago="efectivo"
        )
        await s.commit()

        conv = await convertir_pedido(
            pedido.id, repo=repo, ventas=VentaService(SqlVentasRepository(s)),
            usuario_id=usuario_id,
        )
        await s.commit()
        # Total = 21000 (los deltas ya venían sumados en el snapshot del pedido).
        assert conv.total == Decimal("21000.00")
        desc = (
            await s.execute(
                text("SELECT descripcion FROM ventas_detalle WHERE venta_id = :v "
                     "AND producto_id IS NOT NULL"),
                {"v": conv.venta_id},
            )
        ).scalar_one()
        assert "Carne" in desc and "Adición de queso" in desc


async def test_migracion_0060_up_down(tenant):
    from tools._alembic import downgrade_tenant, upgrade_tenant

    _tbl = "SELECT to_regclass('public.modificador_grupos') IS NOT NULL"
    _col = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name='pedido_items' AND column_name='modificadores'"
    )
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True
        assert (await s.execute(text(_col))).scalar_one() == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0059_pedido_venta")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is False
        assert (await s.execute(text(_col))).scalar_one() == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)   # reaplica head limpio
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True


async def test_tool_armar_pedido_acepta_modificadores_y_mapea_errores(tenant):
    # Capa de tools del bot: el arg `modificadores` viaja al motor; el error vuelve RECUPERABLE
    # con sugerencias (el bot pregunta, jamás inventa).
    from ai.envelope import Contexto, ErrorTool
    from ai.pedidos_tools import PedidosDeps, ejecutar
    from core.llm.base import ToolCall

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _seed_menu(s)
        ctx = Contexto(
            tenant_id=1, usuario_id=1, rol="vendedor", cliente_telefono="3001112233",
            capacidades=frozenset({"pack_pedidos"}),
        )
        deps = PedidosDeps(pedidos=_svc(s))

        ok = await ejecutar(
            ToolCall(id="t1", name="armar_pedido", arguments={
                "items": [{"producto": "hamburguesa", "cantidad": 1,
                           "modificadores": ["carne", "adición de queso"]}],
            }),
            ctx, deps,
        )
        assert not isinstance(ok, ErrorTool), getattr(ok, "detalle", None)
        assert ok.data["items"][0]["modificadores"] == ["Carne", "Adición de queso"]
        assert ok.data["subtotal"] == "21000.00"

        err = await ejecutar(
            ToolCall(id="t2", name="armar_pedido", arguments={
                "items": [{"producto": "hamburguesa", "cantidad": 1,
                           "modificadores": ["carne", "sin kriptonita"]}],
                "pedido_adicional": True,
            }),
            ctx, deps,
        )
        assert isinstance(err, ErrorTool) and err.recuperable
        assert "kriptonita" in err.detail and "Sin cebolla" in err.detail
