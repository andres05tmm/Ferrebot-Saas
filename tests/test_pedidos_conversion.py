"""Puente pedido → venta (F1 Pack Restaurante, ADR 0032; cierra el "v2" del ADR 0016).

Invariantes críticos (test-primero): idempotencia (doble/concurrente NO duplica venta), "nada mueve
stock sin movimiento" (línea de catálogo descuenta VÍA movimiento SALIDA; varia no) y "nada mueve
caja sin registro" (el arqueo híbrido cuadra por `ventas_efectivo`, sin fila en `caja_movimientos`).
Patrón calcado de tests/test_agenda_cobro.py (ADR 0022).
"""
import asyncio
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.pedidos.conversion import PedidoNoConvertible, convertir_pedido
from modules.pedidos.errors import PedidoInexistente
from modules.pedidos.repository import SqlPedidosRepository
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.service import VentaService


async def _seed(
    s: AsyncSession, *, estado="confirmado", metodo_pago="efectivo",
    costo_domicilio="3000", stock="50",
) -> tuple[int, int, int]:
    """(usuario_id, producto_id, pedido_id): vendedor + producto con inventario + pedido en `estado`."""
    usuario_id = (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Cocina','vendedor') RETURNING id")
        )
    ).scalar_one()
    producto_id = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES ('Hamburguesa', 'unidad', 18000, 0, false, true) RETURNING id"
            )
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p, :s, 0)"),
        {"p": producto_id, "s": stock},
    )
    pedido_id = (
        await s.execute(
            text(
                "INSERT INTO pedidos (cliente_telefono, estado, metodo_pago, subtotal, "
                "costo_domicilio, total, direccion) "
                "VALUES ('3001112233', :est, :mp, 36000, :dom, :tot, 'Cra 1 # 2-3') "
                "RETURNING id"
            ),
            {
                "est": estado, "mp": metodo_pago, "dom": costo_domicilio,
                "tot": str(Decimal("36000") + Decimal(costo_domicilio)),
            },
        )
    ).scalar_one()
    await s.execute(
        text(
            "INSERT INTO pedido_items (pedido_id, producto_id, nombre, cantidad, precio_unitario, subtotal) "
            "VALUES (:pe, :pr, 'Hamburguesa', 2, 18000, 36000)"
        ),
        {"pe": pedido_id, "pr": producto_id},
    )
    await s.commit()
    return usuario_id, producto_id, pedido_id


async def _convertir(s: AsyncSession, pedido_id: int, usuario_id: int, **kw):
    res = await convertir_pedido(
        pedido_id,
        repo=SqlPedidosRepository(s),
        ventas=VentaService(SqlVentasRepository(s)),
        usuario_id=usuario_id,
        **kw,
    )
    await s.commit()
    return res


async def test_convertir_crea_venta_descuenta_stock_y_entrega(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, producto_id, pedido_id = await _seed(s)
        res = await _convertir(s, pedido_id, usuario_id)

        assert res.replay is False and res.total == Decimal("39000.00")  # 2×18000 + 3000 domicilio
        fila = (
            await s.execute(
                text(
                    "SELECT v.total, v.metodo_pago, v.idempotency_key, p.estado, p.venta_id, "
                    "p.convertido_en FROM ventas v JOIN pedidos p ON p.venta_id = v.id WHERE p.id = :p"
                ),
                {"p": pedido_id},
            )
        ).one()
        assert fila.total == Decimal("39000.00") and fila.metodo_pago == "efectivo"
        assert fila.idempotency_key == f"pedido-venta:{pedido_id}"
        assert fila.estado == "entregado" and fila.venta_id == res.venta_id
        assert fila.convertido_en is not None

        # INVARIANTE stock: bajó EXACTAMENTE lo que dice el movimiento SALIDA (regla #7).
        stock = (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id = :p"), {"p": producto_id}
            )
        ).scalar_one()
        assert stock == Decimal("48.000")
        salida = (
            await s.execute(
                text(
                    "SELECT sum(cantidad) FROM movimientos_inventario "
                    "WHERE producto_id = :p AND tipo = 'SALIDA'"
                ),
                {"p": producto_id},
            )
        ).scalar_one()
        assert salida == Decimal("2.000")

        # El domicilio va como línea VARIA (sin producto ni stock).
        varia = (
            await s.execute(
                text(
                    "SELECT descripcion, precio_unitario FROM ventas_detalle "
                    "WHERE venta_id = :v AND producto_id IS NULL"
                ),
                {"v": res.venta_id},
            )
        ).one()
        assert "omicilio" in varia.descripcion and varia.precio_unitario == Decimal("3000.00")


async def test_doble_conversion_es_replay_y_no_duplica(tenant):
    # INVARIANTE idempotencia: reintentar devuelve la MISMA venta y el stock solo baja UNA vez.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, producto_id, pedido_id = await _seed(s)
        primero = await _convertir(s, pedido_id, usuario_id)
        segundo = await _convertir(s, pedido_id, usuario_id)

        assert segundo.replay is True and segundo.venta_id == primero.venta_id
        n = (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one()
        assert n == 1
        stock = (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id = :p"), {"p": producto_id}
            )
        ).scalar_one()
        assert stock == Decimal("48.000")


async def test_conversiones_concurrentes_una_sola_venta(tenant):
    # INVARIANTE idempotencia bajo carrera: dos conversiones EN PARALELO (sesiones distintas,
    # FOR UPDATE serializa) → una gana, la otra replaya la misma venta.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, producto_id, pedido_id = await _seed(s)

    async def _una():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s2:
            return await _convertir(s2, pedido_id, usuario_id)

    r1, r2 = await asyncio.gather(_una(), _una())
    assert {r1.replay, r2.replay} == {True, False}
    assert r1.venta_id == r2.venta_id

    async with AsyncSession(tenant.engine) as s:
        n = (await s.execute(text("SELECT count(*) FROM ventas"))).scalar_one()
        stock = (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id = :p"), {"p": producto_id}
            )
        ).scalar_one()
    assert n == 1 and stock == Decimal("48.000")


async def test_arqueo_cuadra_y_caja_movimientos_queda_vacia(tenant):
    # INVARIANTE caja: abrir con 10.000 + venta efectivo 39.000 → esperado 49.000, diferencia 0,
    # y NINGUNA fila en caja_movimientos (el arqueo híbrido cuadra por ventas_efectivo).
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, _, pedido_id = await _seed(s)
        caja = CajaService(SqlCajaRepository(s))
        await caja.abrir(usuario_id=usuario_id, saldo_inicial=Decimal("10000"))
        await s.commit()

        await _convertir(s, pedido_id, usuario_id)

        cierre = await caja.cerrar(usuario_id=usuario_id, saldo_contado=Decimal("49000"))
        await s.commit()
        assert cierre.saldo_esperado == Decimal("49000.00")
        assert cierre.diferencia == Decimal("0.00")
        movs_caja = (await s.execute(text("SELECT count(*) FROM caja_movimientos"))).scalar_one()
        assert movs_caja == 0


async def test_estados_no_convertibles(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, _, cancelado_id = await _seed(s, estado="cancelado")
        repo = SqlPedidosRepository(s)
        ventas = VentaService(SqlVentasRepository(s))
        with pytest.raises(PedidoNoConvertible):
            await convertir_pedido(cancelado_id, repo=repo, ventas=ventas, usuario_id=usuario_id)
        with pytest.raises(PedidoInexistente):
            await convertir_pedido(999_999, repo=repo, ventas=ventas, usuario_id=usuario_id)

    # Un borrador (`recibido`) tampoco: aún no está confirmado por el cliente.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, _, borrador_id = await _seed(s, estado="recibido")
        with pytest.raises(PedidoNoConvertible):
            await convertir_pedido(
                borrador_id, repo=SqlPedidosRepository(s),
                ventas=VentaService(SqlVentasRepository(s)), usuario_id=usuario_id,
            )


async def test_producto_desactivado_cae_a_linea_varia_sin_stock(tenant):
    # El snapshot manda: si el producto se desactivó después de confirmar, la venta sale con línea
    # VARIA (precio del pedido) y NO toca stock — jamás se bloquea la conversión por el catálogo.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, producto_id, pedido_id = await _seed(s)
        await s.execute(text("UPDATE productos SET activo = false WHERE id = :p"), {"p": producto_id})
        await s.commit()

        res = await _convertir(s, pedido_id, usuario_id)
        assert res.total == Decimal("39000.00")
        movs = (await s.execute(text("SELECT count(*) FROM movimientos_inventario"))).scalar_one()
        assert movs == 0
        stock = (
            await s.execute(
                text("SELECT stock_actual FROM inventario WHERE producto_id = :p"), {"p": producto_id}
            )
        ).scalar_one()
        assert stock == Decimal("50.000")


async def test_pedido_sin_metodo_pago_exige_parametro(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        usuario_id, _, pedido_id = await _seed(s, metodo_pago=None)
        repo = SqlPedidosRepository(s)
        ventas = VentaService(SqlVentasRepository(s))
        with pytest.raises(PedidoNoConvertible):
            await convertir_pedido(pedido_id, repo=repo, ventas=ventas, usuario_id=usuario_id)
        # Con el método explícito del kanban sí convierte.
        res = await convertir_pedido(
            pedido_id, repo=repo, ventas=ventas, usuario_id=usuario_id, metodo_pago="transferencia"
        )
        await s.commit()
        assert res.replay is False and res.total == Decimal("39000.00")


async def test_endpoint_convertir_http(tenant):
    # E2E HTTP: gating por `ventas` (404 sin la feature), 201 al convertir, 200 replay, 409 cancelado.
    import httpx
    from fastapi import FastAPI
    from httpx import ASGITransport

    from core.auth import Principal, get_current_user
    from core.auth.features import get_capacidades
    from core.db.session import get_tenant_db
    from modules.pedidos.router import router as pedidos_router

    async def _db():
        async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    def _app(caps: frozenset[str]) -> FastAPI:
        app = FastAPI()
        app.include_router(pedidos_router, prefix="/api/v1")
        app.dependency_overrides[get_current_user] = lambda: Principal(user_id=1, tenant="t", rol="vendedor")
        app.dependency_overrides[get_tenant_db] = _db
        app.dependency_overrides[get_capacidades] = lambda: caps
        return app

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        _, _, pedido_id = await _seed(s)
        _, _, cancelado_id = await _seed(s, estado="cancelado")

    sin_ventas = _app(frozenset({"pack_pedidos"}))
    async with httpx.AsyncClient(
        transport=ASGITransport(app=sin_ventas, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        assert (await c.post(f"/api/v1/pedidos/{pedido_id}/convertir", json={})).status_code == 404

    con_ventas = _app(frozenset({"pack_pedidos", "ventas", "caja"}))
    async with httpx.AsyncClient(
        transport=ASGITransport(app=con_ventas, raise_app_exceptions=False), base_url="http://t"
    ) as c:
        r = await c.post(f"/api/v1/pedidos/{pedido_id}/convertir", json={})
        assert r.status_code == 201, r.text
        cuerpo = r.json()
        assert cuerpo["replay"] is False and Decimal(str(cuerpo["total"])) == Decimal("39000.0")

        replay = await c.post(f"/api/v1/pedidos/{pedido_id}/convertir", json={})
        assert replay.status_code == 200 and replay.json()["replay"] is True

        assert (await c.post(f"/api/v1/pedidos/{cancelado_id}/convertir", json={})).status_code == 409
        assert (await c.post("/api/v1/pedidos/999999/convertir", json={})).status_code == 404

        # El pedido quedó entregado y la venta es consultable en el kanban del día a día.
        lista = await c.get("/api/v1/pedidos?estado=entregado")
        assert pedido_id in [p["id"] for p in lista.json()]
