"""Pedidos a proveedor (F2 reforma dashboard) — invariantes críticos (TDD-primero).

Los que mueven dinero/stock: recibir es idempotente (UNA entrada de inventario, UNA cuenta por
pagar, UN egreso de caja por pedido); recibir tras cancelado falla sin efectos; el pago de contado
sin caja abierta falla SIN efectos parciales; el anticipo egresa UNA sola vez y el recibo anticipado
no vuelve a cobrar; el cuadre de inventario progresivo fija el stock al físico y sella
`inventario.cuadrado_at`. Integración contra base efímera real (fixture `tenant`).
"""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import modules.maquinaria.models  # noqa: F401  (registra `maquinas`: FK de gastos)
import modules.obra.models  # noqa: F401  (registra `obras`: FK de compras/gastos)
from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.compras.repository import SqlComprasRepository
from modules.compras.service import ComprasService
from modules.inventario.repository import SqlInventarioRepository
from modules.inventario.service import InventarioService
from modules.pedidos_proveedor.errors import PedidoNoEditable, RecepcionInvalida
from modules.pedidos_proveedor.repository import SqlPedidosProveedorRepository
from modules.pedidos_proveedor.schemas import (
    LineaRecibir,
    PedidoCrear,
    ProveedorRef,
    RecibirPedido,
)
from modules.pedidos_proveedor.service import PedidosProveedorService
from modules.proveedores.repository import SqlProveedoresRepository


def _svc(s: AsyncSession) -> PedidosProveedorService:
    return PedidosProveedorService(
        SqlPedidosProveedorRepository(s),
        compras=ComprasService(SqlComprasRepository(s)),
        compras_repo=SqlComprasRepository(s),
        proveedores=SqlProveedoresRepository(s),
        caja=CajaService(SqlCajaRepository(s)),
        inventario=InventarioService(SqlInventarioRepository(s)),
    )


def _pedido_rapido(**extra) -> PedidoCrear:
    return PedidoCrear(
        proveedor=ProveedorRef(nombre="Ferrisariato"),
        descripcion="50 martillos y lo de siempre",
        monto_estimado=Decimal("500000"),
        **extra,
    )


def _recibo(pid: int, *, cantidad="10", costo="5000", **extra) -> RecibirPedido:
    return RecibirPedido(
        lineas=[LineaRecibir(producto_id=pid, cantidad=Decimal(cantidad), costo=Decimal(costo))],
        **extra,
    )


async def _counts(engine) -> dict[str, int]:
    async with AsyncSession(engine) as s:
        compras = (await s.execute(text("SELECT count(*) FROM compras"))).scalar_one()
        entradas = (
            await s.execute(text("SELECT count(*) FROM movimientos_inventario WHERE tipo='ENTRADA'"))
        ).scalar_one()
        facturas = (await s.execute(text("SELECT count(*) FROM facturas_proveedores"))).scalar_one()
        egresos = (
            await s.execute(text("SELECT count(*) FROM caja_movimientos WHERE tipo='egreso'"))
        ).scalar_one()
    return {"compras": compras, "entradas": entradas, "facturas": facturas, "egresos": egresos}


# --- Recepción a crédito: compra + CxP, idempotente --------------------------

async def test_recibir_credito_crea_compra_y_cxp_y_es_replay_idempotente(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.commit()
        pedido = (await _svc(s).crear(_pedido_rapido(), usuario_id=uid)).pedido
        await s.commit()

    recibo = _recibo(pid, condicion_pago="credito", numero_factura="F-77",
                     fecha_vencimiento=date(2026, 8, 1))
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).recibir(pedido.id, recibo, usuario_id=uid)
        await s.commit()

    assert r1.replay is False
    assert r1.factura_proveedor_id == "F-77"
    assert r1.pedido.estado == "recibido"
    assert r1.pedido.lead_time_horas is not None and r1.pedido.lead_time_horas >= 0

    async with AsyncSession(tenant.engine) as s:
        pendiente = (
            await s.execute(text("SELECT pendiente FROM facturas_proveedores WHERE id='F-77'"))
        ).scalar_one()
        stock = (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()
    assert Decimal(pendiente) == Decimal("50000.00")   # 10 × 5000: deuda = total real
    assert stock == Decimal("10.000")                  # la ENTRADA movió el inventario

    # Reintento con la MISMA sustancia (doble clic / retry de red): replay, sin duplicar nada.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).recibir(pedido.id, recibo, usuario_id=uid)
        await s.commit()
    assert r2.replay is True
    c = await _counts(tenant.engine)
    assert c["compras"] == 1 and c["entradas"] == 1 and c["facturas"] == 1


async def test_recibir_payload_distinto_tras_recibido_es_conflicto(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        pedido = (await _svc(s).crear(_pedido_rapido(), usuario_id=uid)).pedido
        await _svc(s).recibir(pedido.id, _recibo(pid, condicion_pago="credito"), usuario_id=uid)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PedidoNoEditable):
            await _svc(s).recibir(
                pedido.id, _recibo(pid, costo="9999", condicion_pago="credito"), usuario_id=uid
            )
        await s.rollback()
    c = await _counts(tenant.engine)
    assert c["compras"] == 1 and c["entradas"] == 1


async def test_recibir_tras_cancelado_falla_sin_efectos(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        pedido = (await _svc(s).crear(_pedido_rapido(), usuario_id=uid)).pedido
        await _svc(s).cancelar(pedido.id, usuario_id=uid)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(PedidoNoEditable):
            await _svc(s).recibir(pedido.id, _recibo(pid, condicion_pago="credito"), usuario_id=uid)
        await s.rollback()
    c = await _counts(tenant.engine)
    assert c["compras"] == 0 and c["entradas"] == 0 and c["facturas"] == 0


# --- Contado: egreso de caja, atómico --------------------------------------

async def test_recibir_contado_sin_caja_abierta_falla_sin_efectos_parciales(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        pedido = (await _svc(s).crear(_pedido_rapido(), usuario_id=uid)).pedido
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CajaNoAbierta):
            await _svc(s).recibir(
                pedido.id, _recibo(pid, condicion_pago="contado", pago_desde_caja=True), usuario_id=uid
            )
        await s.rollback()

    c = await _counts(tenant.engine)
    assert c == {"compras": 0, "entradas": 0, "facturas": 0, "egresos": 0}
    async with AsyncSession(tenant.engine) as s:
        estado = (
            await s.execute(text("SELECT estado FROM pedidos_proveedor WHERE id=:i"), {"i": pedido.id})
        ).scalar_one()
    assert estado == "pedido"   # el pedido sigue en camino: se puede reintentar tras abrir caja


async def test_recibir_contado_con_caja_egresa_una_sola_vez(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        pedido = (await _svc(s).crear(_pedido_rapido(), usuario_id=uid)).pedido
        await s.commit()

    recibo = _recibo(pid, condicion_pago="contado", pago_desde_caja=True)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).recibir(pedido.id, recibo, usuario_id=uid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).recibir(pedido.id, recibo, usuario_id=uid)   # retry
        await s.commit()

    assert r2.replay is True
    c = await _counts(tenant.engine)
    assert c["egresos"] == 1 and c["compras"] == 1
    async with AsyncSession(tenant.engine) as s:
        monto = (
            await s.execute(text("SELECT monto FROM caja_movimientos WHERE tipo='egreso'"))
        ).scalar_one()
    assert Decimal(monto) == Decimal("50000.00")


# --- Anticipo (proveedores que cobran al pedir) ------------------------------

async def test_anticipo_egresa_una_vez_y_recibo_anticipado_no_vuelve_a_cobrar(tenant, seed_producto):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        await s.commit()

    datos = _pedido_rapido(
        anticipo=Decimal("50000"), anticipo_desde_caja=True, idempotency_key="ped-ant-1"
    )
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).crear(datos, usuario_id=uid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).crear(datos, usuario_id=uid)   # doble clic del alta
        await s.commit()

    assert r1.replay is False and r2.replay is True and r2.pedido.id == r1.pedido.id
    c = await _counts(tenant.engine)
    assert c["egresos"] == 1   # UN solo egreso de anticipo

    # Recibo anticipado con costo == anticipo: NO genera egreso adicional ni deuda.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r = await _svc(s).recibir(
            r1.pedido.id, _recibo(pid, condicion_pago="anticipado"), usuario_id=uid
        )
        await s.commit()
    assert r.factura_proveedor_id is None
    c = await _counts(tenant.engine)
    assert c["egresos"] == 1 and c["facturas"] == 0 and c["entradas"] == 1


async def test_anticipado_con_remanente_a_credito_descuenta_el_anticipo(tenant, seed_producto):
    """Anticipo 20.000, mercancía real 50.000 a crédito: la factura nace por 50.000 con un abono
    automático de 20.000 (el anticipo ya entregado) → pendiente 30.000. Contabilidad completa."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        pedido = (
            await _svc(s).crear(_pedido_rapido(anticipo=Decimal("20000")), usuario_id=uid)
        ).pedido
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r = await _svc(s).recibir(
            pedido.id,
            _recibo(pid, condicion_pago="anticipado", numero_factura="F-ANT-1"),
            usuario_id=uid,
        )
        await s.commit()

    assert r.factura_proveedor_id == "F-ANT-1"
    async with AsyncSession(tenant.engine) as s:
        total, pagado, pendiente = (
            await s.execute(
                text("SELECT total, pagado, pendiente FROM facturas_proveedores WHERE id='F-ANT-1'")
            )
        ).one()
    assert Decimal(total) == Decimal("50000.00")
    assert Decimal(pagado) == Decimal("20000.00")
    assert Decimal(pendiente) == Decimal("30000.00")


async def test_anticipado_con_remanente_sin_destino_es_invalido(tenant, seed_producto):
    """Total real > anticipo y la recepción no dice cómo se paga el resto (ni caja ni factura):
    422 — la plata no puede desaparecer del registro (contabilidad total)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        pedido = (
            await _svc(s).crear(_pedido_rapido(anticipo=Decimal("20000")), usuario_id=uid)
        ).pedido
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(RecepcionInvalida):
            await _svc(s).recibir(
                pedido.id, _recibo(pid, condicion_pago="anticipado"), usuario_id=uid
            )
        await s.rollback()
    c = await _counts(tenant.engine)
    assert c["compras"] == 0 and c["entradas"] == 0


# --- Inventario progresivo: cuadre al recibir --------------------------------

async def test_cuadre_al_recibir_fija_stock_fisico_y_sella_cuadrado_at(tenant, seed_producto):
    """Producto con stock -25 (se vendió sin inventario registrado). Llegan 50 y 'se había acabado':
    físico = 50. La recepción aplica ENTRADA (+50 → 25) y el cuadre AJUSTE (+25 → 50), y sella
    `cuadrado_at` (el producto pasa a inventario confiable)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="0")
        await s.execute(
            text("UPDATE inventario SET stock_actual = -25 WHERE producto_id=:p"), {"p": pid}
        )
        await s.commit()
        pedido = (await _svc(s).crear(_pedido_rapido(), usuario_id=uid)).pedido
        await s.commit()

    recibo = RecibirPedido(
        lineas=[LineaRecibir(
            producto_id=pid, cantidad=Decimal("50"), costo=Decimal("5000"),
            cantidad_fisica=Decimal("50"),
        )],
        condicion_pago="credito",
    )
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r = await _svc(s).recibir(pedido.id, recibo, usuario_id=uid)
        await s.commit()

    (linea,) = r.lineas
    assert linea.stock_previo == Decimal("-25.000")
    assert linea.stock_resultante == Decimal("50.000")
    assert linea.cuadrado is True

    async with AsyncSession(tenant.engine) as s:
        stock, cuadrado_at = (
            await s.execute(
                text("SELECT stock_actual, cuadrado_at FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).one()
        ajustes = (
            await s.execute(
                text("SELECT COALESCE(SUM(cantidad),0) FROM movimientos_inventario "
                     "WHERE tipo='AJUSTE' AND producto_id=:p"), {"p": pid}
            )
        ).scalar_one()
    assert stock == Decimal("50.000")
    assert cuadrado_at is not None
    assert Decimal(ajustes) == Decimal("25.000")   # el cuadre quedó trazado en el kárdex (regla #7)


async def test_conteo_manual_sella_cuadrado_at(tenant, seed_producto):
    """El conteo físico de un solo producto (acción "Cuadrar" de inventario) también sella la marca."""
    from core.config.timezone import now_co  # noqa: F401  (documenta la TZ del sello)

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s, stock="10")
        await s.commit()
        await InventarioService(SqlInventarioRepository(s)).contar(
            producto_id=pid, cantidad_contada=Decimal("10"), usuario_id=uid,
        )   # delta 0 (no-op de stock) — pero el conteo CONFIRMA el físico: sella igual
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        cuadrado_at = (
            await s.execute(
                text("SELECT cuadrado_at FROM inventario WHERE producto_id=:p"), {"p": pid}
            )
        ).scalar_one()
    assert cuadrado_at is not None


# --- Puente compra directa → cuenta por pagar --------------------------------

async def test_compra_directa_a_credito_crea_cxp_una_sola_vez(tenant, seed_producto):
    from modules.compras.schemas import CompraCrear, CompraItemCrear
    from modules.compras.schemas import ProveedorRef as CompraProveedorRef

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid, pid = await seed_producto(s)
        await s.commit()
        datos = CompraCrear(
            proveedor=CompraProveedorRef(nombre="Ferrisariato"),
            items=[CompraItemCrear(producto_id=pid, cantidad=Decimal("4"), costo=Decimal("2500"))],
            idempotency_key="compra-cred-1",
            a_credito=True, numero_factura="FC-1", fecha_vencimiento=date(2026, 8, 15),
        )
        svc = ComprasService(SqlComprasRepository(s), proveedores=SqlProveedoresRepository(s))
        r1 = await svc.registrar(datos, usuario_id=uid)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        svc = ComprasService(SqlComprasRepository(s), proveedores=SqlProveedoresRepository(s))
        r2 = await svc.registrar(datos, usuario_id=uid)   # retry misma key
        await s.commit()

    assert r1.replay is False and r2.replay is True
    async with AsyncSession(tenant.engine) as s:
        n, pendiente = (
            await s.execute(
                text("SELECT count(*), COALESCE(SUM(pendiente),0) FROM facturas_proveedores")
            )
        ).one()
    assert n == 1
    assert Decimal(pendiente) == Decimal("10000.00")   # 4 × 2500
