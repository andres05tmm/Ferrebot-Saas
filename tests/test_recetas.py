"""Recetas/BOM + impuestos + recargo por plato (F6 Pack Restaurante, ADR 0032 D2/D8/D9).

Invariantes (test-primero): vender un plato con receta genera movimientos SALIDA de TODOS sus
insumos (con costo) y NINGUNO del plato; el replay no duplica movimientos; insumo insuficiente
ALERTA pero no bloquea (stock negativo honesto); producto sin receta se comporta como hoy.
Además: el impoconsumo 8% se modela (`tipo_impuesto='inc'` con tarifa en `iva`, snapshot en el
detalle) y el recargo POR PLATO de zona (Bocagrande) se suma a la tarifa plana del domicilio.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.pedidos.conversion import convertir_pedido
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido, PedidosService
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService


async def _seed(s: AsyncSession) -> dict:
    """Plato $19.000 (INC 8%, SIN inventario, receta: 0.2 arroz + 0.25 carne) + Limonada normal."""
    await s.execute(
        text(
            "INSERT INTO pedido_config (activo, hora_apertura, hora_cierre, minimo_pedido, "
            "tiempo_estimado_min, costo_domicilio_default) VALUES (true, '00:00', '23:59', 0, 45, 2000)"
        )
    )
    ids = {}
    ids["usuario"] = (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Caja','vendedor') RETURNING id")
        )
    ).scalar_one()
    # Insumos CON inventario y costo promedio.
    for nombre, stock, costo in (("Arroz", "10", "2000"), ("Carne", "5", "8000")):
        ids[nombre] = (
            await s.execute(
                text(
                    "INSERT INTO productos (nombre, unidad_medida, precio_venta, costo_promedio, "
                    "iva, permite_fraccion, activo) "
                    "VALUES (:n, 'kg', 0, :c, 0, true, true) RETURNING id"
                ),
                {"n": nombre, "c": costo},
            )
        ).scalar_one()
        await s.execute(
            text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, :s, 0)"),
            {"p": ids[nombre], "s": stock},
        )
    # El PLATO: precio final con INC 8% incluido; NO lleva inventario (el stock es de los insumos).
    ids["Plato"] = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, tipo_impuesto, "
                "permite_fraccion, activo) "
                "VALUES ('Plato fuerte', 'unidad', 19000, 8, 'inc', false, true) RETURNING id"
            )
        )
    ).scalar_one()
    for insumo, cantidad in (("Arroz", "0.2"), ("Carne", "0.25")):
        await s.execute(
            text(
                "INSERT INTO recetas (producto_id, insumo_id, cantidad) VALUES (:p, :i, :c)"
            ),
            {"p": ids["Plato"], "i": ids[insumo], "c": cantidad},
        )
    # Limonada normal (IVA 0, con stock propio) — regresión: sin receta, igual que hoy.
    ids["Limonada"] = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES ('Limonada', 'unidad', 5000, 0, false, true) RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, 50, 0)"),
        {"p": ids["Limonada"]},
    )
    await s.commit()
    return ids


async def _pedido_confirmado(s: AsyncSession, items) -> int:
    svc = PedidosService(SqlPedidosRepository(s))
    await svc.armar_pedido("3001112233", items, ahora=now_co())
    pedido, _ = await svc.confirmar_pedido(
        "3001112233", direccion="Cra 1 # 2-3", metodo_pago="efectivo"
    )
    await s.commit()
    return pedido.id


async def test_plato_con_receta_descuenta_insumos_y_no_el_plato(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        # El plato no lleva stock: armar_pedido no debe frenarlo por stock (0 del plato).
        pedido_id = await _pedido_confirmado(s, [
            ItemPedido(producto="plato fuerte", cantidad=Decimal("2")),
            ItemPedido(producto="limonada", cantidad=Decimal("1")),
        ])
        repo = SqlPedidosRepository(s)
        res = await convertir_pedido(
            pedido_id, repo=repo, ventas=VentaService(SqlVentasRepository(s)),
            usuario_id=ids["usuario"],
        )
        await s.commit()
        assert res.total == Decimal("45000.00")   # 2×19000 + 5000 + 2000 domicilio

        # Movimientos: SALIDA de TODOS los insumos (0.4 arroz, 0.5 carne) con costo; NINGUNO del plato.
        movs = (
            await s.execute(
                text(
                    "SELECT producto_id, tipo, cantidad, costo_unitario FROM movimientos_inventario "
                    "ORDER BY producto_id"
                )
            )
        ).all()
        por_producto = {m.producto_id: m for m in movs}
        assert ids["Plato"] not in por_producto
        assert por_producto[ids["Arroz"]].cantidad == Decimal("0.400")
        assert por_producto[ids["Arroz"]].costo_unitario == Decimal("2000.00")
        assert por_producto[ids["Carne"]].cantidad == Decimal("0.500")
        assert por_producto[ids["Limonada"]].cantidad == Decimal("1.000")   # sin receta: como hoy
        # Stock de insumos bajó EXACTO lo del movimiento (regla #7).
        stock_arroz = (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id = :p"), {"p": ids["Arroz"]}
            )
        ).scalar_one()
        assert stock_arroz == Decimal("9.600")

        # Impoconsumo modelado: el detalle del plato snapshotea tipo_impuesto='inc' tarifa 8.
        det = (
            await s.execute(
                text(
                    "SELECT iva, tipo_impuesto FROM ventas_detalle WHERE venta_id = :v "
                    "AND descripcion LIKE 'Plato%'"
                ),
                {"v": res.venta_id},
            )
        ).one()
        assert det.iva == 8 and det.tipo_impuesto == "inc"

        # Replay: reintentar la conversión NO duplica movimientos de insumos.
        res2 = await convertir_pedido(
            pedido_id, repo=repo, ventas=VentaService(SqlVentasRepository(s)),
            usuario_id=ids["usuario"],
        )
        await s.commit()
        assert res2.replay is True
        n = (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one()
        assert n == 3   # arroz + carne + limonada, una sola vez


async def test_insumo_insuficiente_alerta_pero_no_bloquea(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        await s.execute(
            text("UPDATE inventario SET stock_actual = 0.1 WHERE producto_id = :p"), {"p": ids["Carne"]}
        )
        await s.commit()
        pedido_id = await _pedido_confirmado(s, [ItemPedido(producto="plato fuerte", cantidad=Decimal("2"))])
        res = await convertir_pedido(
            pedido_id, repo=SqlPedidosRepository(s),
            ventas=VentaService(SqlVentasRepository(s)), usuario_id=ids["usuario"],
        )
        await s.commit()
        # La venta PASÓ y el stock quedó negativo honesto + alerta del insumo (política ADR 0032 D9).
        assert res.replay is False
        assert any("Carne" in a for a in res.alertas)
        stock = (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id = :p"), {"p": ids["Carne"]}
            )
        ).scalar_one()
        assert stock == Decimal("-0.400")


async def test_recargo_por_plato_bocagrande(tenant):
    # D8: zona con tarifa plana 3000 + recargo POR PLATO 1000 → 3 platos = 3000 + 3×1000 = 6000.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        await s.execute(
            text(
                "INSERT INTO zonas_domicilio (nombre, tarifa, recargo_por_item, activo) "
                "VALUES ('Bocagrande', 3000, 1000, true)"
            )
        )
        await s.commit()
        svc = PedidosService(SqlPedidosRepository(s))
        await svc.armar_pedido(
            "3005556677", [ItemPedido(producto="plato fuerte", cantidad=Decimal("3"))], ahora=now_co()
        )
        pedido, _ = await svc.confirmar_pedido(
            "3005556677", direccion="Cl 1", barrio="Bocagrande", metodo_pago="efectivo"
        )
        await s.commit()
        assert pedido.costo_domicilio == Decimal("6000.00")
        assert pedido.total == Decimal("63000.00")   # 3×19000 + 6000


async def test_costo_plato_y_router_recetas(tenant):
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    from core.auth import Principal, get_current_user
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db
    from modules.pedidos.recetas_router import router as recetas_router

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    def _app(caps: frozenset[str], rol: str = "vendedor") -> FastAPI:
        app = FastAPI()
        app.include_router(recetas_router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="t", rol=rol)
        app.dependency_overrides[get_tenant_db] = _db
        app.dependency_overrides[get_capacidades] = lambda: caps
        return app

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)

    # Sin flag `recetas` → 404.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(frozenset({"inventario", "ventas"})), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        assert (await c.get(f"/api/v1/recetas/{ids['Plato']}")).status_code == 404

    caps = frozenset({"recetas", "inventario", "ventas"})
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(caps), raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.get(f"/api/v1/recetas/{ids['Plato']}")
        assert r.status_code == 200, r.text
        cuerpo = r.json()
        # Costo del plato = Σ costo_promedio × cantidad = 0.2×2000 + 0.25×8000 = 2400.
        assert Decimal(str(cuerpo["costo_plato"])) == Decimal("2400.0")
        assert len(cuerpo["insumos"]) == 2
        # Editar la receta es de admin.
        assert (
            await c.put(f"/api/v1/recetas/{ids['Plato']}", json={"insumos": []})
        ).status_code == 403

    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(caps, rol="admin"), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        r = await c.put(
            f"/api/v1/recetas/{ids['Plato']}",
            json={"insumos": [{"insumo_id": ids["Arroz"], "cantidad": "0.3"}]},
        )
        assert r.status_code == 200 and len(r.json()["insumos"]) == 1


async def test_migracion_0063_up_down(tenant):
    from tools._alembic import downgrade_tenant, upgrade_tenant

    _tbl = "SELECT to_regclass('public.recetas') IS NOT NULL"
    _col = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name='productos' AND column_name='tipo_impuesto'"
    )
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True
        assert (await s.execute(text(_col))).scalar_one() == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0062_kds")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is False
        assert (await s.execute(text(_col))).scalar_one() == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True
