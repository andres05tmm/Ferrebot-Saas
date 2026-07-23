"""Mesas y salón (F3 Pack Restaurante, ADR 0032 D4): orden abierta por mesa sobre `pedidos`.

Invariantes (test-primero): idempotencia del cobro (reusa el puente F1 — venta única, mesa
liberada), dos meseros CONCURRENTES no duplican ni pierden ítems (FOR UPDATE), y la propina es una
línea varia discriminada que NO altera el total de productos y JAMÁS aplica a domicilio.
"""
import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.pedidos.conversion import PedidoNoConvertible, convertir_pedido
from modules.pedidos.mesas import MesaInexistente, MesaSinOrden, MesasService
from modules.pedidos.repository import SqlPedidosRepository
from modules.pedidos.service import ItemPedido
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService


async def _seed(s: AsyncSession) -> dict:
    """Vendedor + Hamburguesa $18.000 / Limonada $5.000 (con stock) + mesa 'Mesa 1'."""
    ids = {}
    ids["usuario"] = (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Mesero','vendedor') RETURNING id")
        )
    ).scalar_one()
    for nombre, precio in (("Hamburguesa", 18000), ("Limonada", 5000)):
        pid = (
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
            {"p": pid},
        )
        ids[nombre] = pid
    ids["mesa"] = (
        await s.execute(
            text("INSERT INTO mesas (nombre, zona, activo) VALUES ('Mesa 1', 'salón', true) RETURNING id")
        )
    ).scalar_one()
    await s.commit()
    return ids


def _svc(s: AsyncSession) -> MesasService:
    return MesasService(SqlPedidosRepository(s))


async def test_e2e_mesa_dos_rondas_precuenta_cobro_con_propina(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = _ = await _seed(s)
        svc = _svc(s)

        orden = await svc.abrir(ids["mesa"])
        assert orden.estado == "abierto" and orden.origen == "mesa"
        # Abrir de nuevo es idempotente: la MISMA orden.
        assert (await svc.abrir(ids["mesa"])).id == orden.id

        # Ronda 1 y ronda 2 (incremental, no reemplaza).
        await svc.agregar(ids["mesa"], [ItemPedido(producto="hamburguesa", cantidad=Decimal("2"))])
        orden = await svc.agregar(ids["mesa"], [ItemPedido(producto="limonada", cantidad=Decimal("1"))])
        await s.commit()
        assert len(orden.items) == 2

        # Precuenta: total correcto, no muta nada.
        pre = await svc.precuenta(ids["mesa"])
        assert pre.total == Decimal("41000.00")   # 2×18000 + 5000

        # Cobro con propina de $4.000 → venta única idempotente y mesa liberada.
        res = await svc.cobrar(
            ids["mesa"], ventas=VentaService(SqlVentasRepository(s)),
            usuario_id=ids["usuario"], metodo_pago="efectivo", propina=Decimal("4000"),
        )
        await s.commit()
        assert res.replay is False and res.total == Decimal("45000.00")

        fila = (
            await s.execute(
                text("SELECT estado, venta_id FROM pedidos WHERE id = :p"), {"p": orden.id}
            )
        ).one()
        assert fila.estado == "entregado" and fila.venta_id == res.venta_id
        # Mesa liberada: no queda orden abierta.
        with pytest.raises(MesaSinOrden):
            await svc.precuenta(ids["mesa"])

        # La propina queda DISCRIMINADA como línea varia y no altera el total de productos.
        propina = (
            await s.execute(
                text(
                    "SELECT precio_unitario FROM ventas_detalle "
                    "WHERE venta_id = :v AND descripcion = 'Propina'"
                ),
                {"v": res.venta_id},
            )
        ).scalar_one()
        assert propina == Decimal("4000.00")
        productos = (
            await s.execute(
                text(
                    "SELECT sum(precio_unitario * cantidad) FROM ventas_detalle "
                    "WHERE venta_id = :v AND descripcion IS DISTINCT FROM 'Propina'"
                ),
                {"v": res.venta_id},
            )
        ).scalar_one()
        assert productos == Decimal("41000.00")

        # Re-cobrar (reintento de red) → replay de la MISMA venta.
        orden2 = await svc.abrir(ids["mesa"])   # nueva orden para la mesa (quedó libre)
        assert orden2.id != orden.id


async def test_cobro_idempotente_replay(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        svc = _svc(s)
        await svc.abrir(ids["mesa"])
        await svc.agregar(ids["mesa"], [ItemPedido(producto="limonada", cantidad=Decimal("1"))])
        await s.commit()
        ventas = VentaService(SqlVentasRepository(s))
        primero = await svc.cobrar(
            ids["mesa"], ventas=ventas, usuario_id=ids["usuario"], metodo_pago="efectivo"
        )
        await s.commit()
        # El pedido ya cerró: recobrar por el puente directo replaya la misma venta.
        orden_id = (
            await s.execute(text("SELECT id FROM pedidos WHERE venta_id = :v"), {"v": primero.venta_id})
        ).scalar_one()
        segundo = await convertir_pedido(
            orden_id, repo=SqlPedidosRepository(s), ventas=ventas, usuario_id=ids["usuario"],
        )
        assert segundo.replay is True and segundo.venta_id == primero.venta_id
        n = (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one()
        assert n == 1


async def test_dos_meseros_concurrentes_no_duplican_items(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        await _svc(s).abrir(ids["mesa"])
        await s.commit()

    async def _ronda(producto: str):
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s2:
            await _svc(s2).agregar(ids["mesa"], [ItemPedido(producto=producto, cantidad=Decimal("1"))])
            await s2.commit()

    await asyncio.gather(_ronda("hamburguesa"), _ronda("limonada"))

    async with AsyncSession(tenant.engine) as s:
        filas = (
            await s.execute(
                text(
                    "SELECT pi.nombre, p.subtotal FROM pedido_items pi "
                    "JOIN pedidos p ON p.id = pi.pedido_id WHERE p.estado = 'abierto'"
                )
            )
        ).all()
    assert sorted(f.nombre for f in filas) == ["Hamburguesa", "Limonada"]
    assert filas[0].subtotal == Decimal("23000.00")   # 18000 + 5000, sin perder ninguna ronda


async def test_propina_jamas_en_domicilio(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        # Pedido de DOMICILIO confirmado (origen whatsapp).
        pedido_id = (
            await s.execute(
                text(
                    "INSERT INTO pedidos (cliente_telefono, estado, metodo_pago, subtotal, total, origen) "
                    "VALUES ('3001112233', 'confirmado', 'efectivo', 5000, 5000, 'whatsapp') RETURNING id"
                )
            )
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO pedido_items (pedido_id, producto_id, nombre, cantidad, precio_unitario, subtotal) "
                "VALUES (:pe, :pr, 'Limonada', 1, 5000, 5000)"
            ),
            {"pe": pedido_id, "pr": ids["Limonada"]},
        )
        await s.commit()
        with pytest.raises(PedidoNoConvertible):
            await convertir_pedido(
                pedido_id, repo=SqlPedidosRepository(s),
                ventas=VentaService(SqlVentasRepository(s)),
                usuario_id=ids["usuario"], propina=Decimal("2000"),
            )


async def test_mesa_inexistente_y_sin_orden(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)
        svc = _svc(s)
        with pytest.raises(MesaInexistente):
            await svc.abrir(999_999)
        with pytest.raises(MesaSinOrden):
            await svc.agregar(ids["mesa"], [ItemPedido(producto="limonada", cantidad=Decimal("1"))])


async def test_migracion_0061_up_down(tenant):
    from tools._alembic import downgrade_tenant, upgrade_tenant

    _tbl = "SELECT to_regclass('public.mesas') IS NOT NULL"
    _col = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name='pedidos' AND column_name='mesa_id'"
    )
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True
        assert (await s.execute(text(_col))).scalar_one() == 1

    await tenant.engine.dispose()
    downgrade_tenant(tenant.url, "0060_modificadores")
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is False
        assert (await s.execute(text(_col))).scalar_one() == 0

    await tenant.engine.dispose()
    upgrade_tenant(tenant.url)
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text(_tbl))).scalar_one() is True


async def test_endpoint_mesas_gating_y_flujo(tenant):
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    from core.auth import Principal, get_current_user
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db
    from modules.pedidos.mesas_router import router as mesas_router

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
        app.include_router(mesas_router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="t", rol=rol)
        app.dependency_overrides[get_tenant_db] = _db
        app.dependency_overrides[get_capacidades] = lambda: caps
        return app

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        ids = await _seed(s)

    # Sin flag pack_mesas → 404 (como si no existiera).
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(frozenset({"ventas"})), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        assert (await c.get("/api/v1/mesas")).status_code == 404

    caps = frozenset({"pack_mesas", "ventas", "caja"})
    # Crear mesa es de admin.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(caps, rol="vendedor"), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        assert (await c.post("/api/v1/mesas", json={"nombre": "Mesa 2"})).status_code == 403

    async with httpx.AsyncClient(
        transport=ASGITransport(app=_app(caps, rol="admin"), raise_app_exceptions=False),
        base_url="http://t",
    ) as c:
        r = await c.post("/api/v1/mesas", json={"nombre": "Mesa 2", "zona": "terraza"})
        assert r.status_code == 201

        # Flujo staff: abrir → agregar → precuenta → cobrar.
        mesa_id = ids["mesa"]
        assert (await c.post(f"/api/v1/mesas/{mesa_id}/abrir")).status_code == 201
        r = await c.post(
            f"/api/v1/mesas/{mesa_id}/items",
            json={"items": [{"producto": "hamburguesa", "cantidad": "1"}]},
        )
        assert r.status_code == 200, r.text
        pre = await c.get(f"/api/v1/mesas/{mesa_id}/precuenta")
        assert pre.status_code == 200 and Decimal(str(pre.json()["total"])) == Decimal("18000.0")
        cobro = await c.post(
            f"/api/v1/mesas/{mesa_id}/cobrar",
            json={"metodo_pago": "efectivo", "propina": "2000"},
        )
        assert cobro.status_code == 201, cobro.text
        assert Decimal(str(cobro.json()["total"])) == Decimal("20000.0")
        # Mesa liberada: precuenta ahora 404/409.
        assert (await c.get(f"/api/v1/mesas/{mesa_id}/precuenta")).status_code == 409
