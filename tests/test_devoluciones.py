"""Devoluciones (ADR 0026, Fase 3 Contable B) — invariantes contra base efímera real (Postgres).

Invariantes críticos (TDD test-primero):
- La devolución SIEMPRE mueve stock + contrapartida de caja/fiado (nunca una sin la otra).
- Idempotencia estricta: misma key + payload → replay sin duplicar; key + payload distinto → 409 (FF-1).
- El COGS no se distorsiona: la devolución re-ingresa al costo del snapshot original, no al promedio.
- El arqueo del día cuadra tras una venta + su devolución (anti doble conteo del arqueo híbrido).
- Aislamiento multi-tenant de lo nuevo.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import rango_dia_co, today_co
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.devoluciones.errors import CajaRequerida, DevolucionConflicto
from modules.devoluciones.repository import SqlDevolucionesRepository
from modules.devoluciones.schemas import DevolucionCrear, DevolucionLineaCrear
from modules.devoluciones.service import DevolucionesService
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService
from modules.reportes.repository import SqlReportesRepository
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService


# --- helpers -----------------------------------------------------------------
async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id"))
    ).scalar_one()


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Cliente', 0) RETURNING id"))
    ).scalar_one()


async def _producto(s: AsyncSession, *, precio="20000", costo="12000", stock="100") -> int:
    pid = (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, precio_compra, costo_promedio, "
                "iva, permite_fraccion, activo) VALUES ('Cemento','unidad',:pv,:pc,:cp,19,false,true) RETURNING id"
            ),
            {"pv": precio, "pc": costo, "cp": costo},
        )
    ).scalar_one()
    await s.execute(
        text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) VALUES (:p,:s,0)"),
        {"p": pid, "s": stock},
    )
    return pid


def _venta(pid, cantidad, *, metodo="efectivo", cliente_id=None):
    return VentaCrear(
        metodo_pago=metodo, cliente_id=cliente_id,
        lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal(cantidad))],
    )


def _svc(s: AsyncSession) -> DevolucionesService:
    return DevolucionesService(
        SqlDevolucionesRepository(s),
        caja=SqlCajaRepository(s),
        fiados=FiadosService(SqlFiadosRepository(s)),
        notas=None,
    )


async def _stock(engine, pid: int) -> Decimal:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(text("SELECT stock_actual FROM inventario WHERE producto_id=:p"), {"p": pid})
        ).scalar_one()


# --- stock + contrapartida (efectivo) ----------------------------------------
async def test_devolucion_total_efectivo_reingresa_stock_y_egresa_caja(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s, precio="20000", costo="12000", stock="100")
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("50000"))
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        await s.commit()

    assert await _stock(tenant.engine, pid) == Decimal("97.000")  # 100 - 3

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).devolver(DevolucionCrear(venta_id=venta.id, motivo="defectuoso"), usuario_id=uid)
        await s.commit()
        dev_id = res.devolucion.id

    assert res.replay is False
    assert await _stock(tenant.engine, pid) == Decimal("100.000")  # re-ingresado

    async with AsyncSession(tenant.engine) as s:
        tipo, cant, costo = (
            await s.execute(
                text("SELECT tipo, cantidad, costo_unitario FROM movimientos_inventario WHERE referencia=:r"),
                {"r": f"devolucion:{dev_id}"},
            )
        ).one()
        assert tipo == "DEVOLUCION" and cant == Decimal("3.000")
        assert costo == Decimal("12000.00")   # snapshot de la SALIDA, no el promedio actual
        # Contrapartida: egreso de caja por el total devuelto.
        tipo_m, monto = (
            await s.execute(
                text("SELECT tipo, monto FROM caja_movimientos WHERE referencia=:r"),
                {"r": f"devolucion:{dev_id}"},
            )
        ).one()
        assert tipo_m == "egreso" and monto == Decimal("60000.00")   # 3 × 20000
        assert (await s.execute(text("SELECT count(*) FROM devoluciones"))).scalar_one() == 1


# --- INVARIANTE: nada mueve stock sin contrapartida --------------------------
async def test_sin_caja_abierta_no_mueve_stock_ni_registra(tenant):
    """Reintegro en efectivo sin caja abierta → CajaRequerida y la transacción entera se revierte."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s, stock="100")
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CajaRequerida):
            await _svc(s).devolver(DevolucionCrear(venta_id=venta.id), usuario_id=uid)
        await s.rollback()

    assert await _stock(tenant.engine, pid) == Decimal("97.000")   # intacto: NO se re-ingresó
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM devoluciones"))).scalar_one() == 0
        movs = (
            await s.execute(text("SELECT count(*) FROM movimientos_inventario WHERE tipo='DEVOLUCION'"))
        ).scalar_one()
        assert movs == 0


# --- INVARIANTE: idempotencia estricta ---------------------------------------
async def test_idempotencia_misma_key_no_duplica(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s, stock="100")
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        await s.commit()

    payload = DevolucionCrear(venta_id=venta.id, idempotency_key="dev-1")
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r1 = await _svc(s).devolver(payload, usuario_id=uid)
        await s.commit()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).devolver(payload, usuario_id=uid)
        await s.commit()

    assert r2.replay is True and r2.devolucion.id == r1.devolucion.id
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM devoluciones"))).scalar_one() == 1
        assert (
            await s.execute(text("SELECT count(*) FROM movimientos_inventario WHERE tipo='DEVOLUCION'"))
        ).scalar_one() == 1
        assert (
            await s.execute(text("SELECT count(*) FROM caja_movimientos WHERE tipo='egreso'"))
        ).scalar_one() == 1
    assert await _stock(tenant.engine, pid) == Decimal("100.000")   # re-ingresado una sola vez


async def test_idempotencia_payload_distinto_409(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s, stock="100")
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).devolver(DevolucionCrear(venta_id=venta.id, idempotency_key="k"), usuario_id=uid)
        await s.commit()

    # Misma key, payload DISTINTO (parcial de 1 en vez de total) → 409.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(DevolucionConflicto):
            await _svc(s).devolver(
                DevolucionCrear(
                    venta_id=venta.id, idempotency_key="k",
                    lineas=[DevolucionLineaCrear(producto_id=pid, cantidad=Decimal("1"))],
                ),
                usuario_id=uid,
            )
        await s.rollback()


# --- INVARIANTE: COGS no se distorsiona --------------------------------------
async def test_cogs_no_se_distorsiona_tras_devolucion(tenant):
    """La devolución re-ingresa al costo del snapshot (12000), aun si el promedio del día cambió;
    el P&L netea SALIDA − DEVOLUCION → COGS 0 para una devolución total."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s, precio="20000", costo="12000", stock="100")
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        # El promedio del día se mueve DESPUÉS de vender (una compra a otro precio): no debe afectar
        # el costo con que re-ingresa la devolución.
        await s.execute(text("UPDATE productos SET costo_promedio = 30000 WHERE id=:p"), {"p": pid})
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).devolver(DevolucionCrear(venta_id=venta.id), usuario_id=uid)
        await s.commit()

    inicio, fin = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine) as s:
        agg = await SqlReportesRepository(s).estado_resultados(inicio=inicio, fin=fin)
    assert agg.costo_ventas == Decimal("0.00")   # 3×12000 (SALIDA) − 3×12000 (DEVOLUCION)


# --- INVARIANTE: arqueo cuadra tras venta + devolución -----------------------
async def test_arqueo_cuadra_tras_venta_y_devolucion(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s, precio="20000", costo="12000", stock="100")
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("50000"))
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).devolver(DevolucionCrear(venta_id=venta.id), usuario_id=uid)
        await s.commit()

    # La venta suma +60000 (ventas_efectivo) y la devolución resta −60000 (egreso): esperado = inicial.
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        caja = await CajaService(SqlCajaRepository(s)).cerrar(usuario_id=uid, saldo_contado=Decimal("50000"))
        await s.commit()

    assert caja.saldo_esperado == Decimal("50000.00")
    assert caja.diferencia == Decimal("0.00")


# --- reintegro a fiado -------------------------------------------------------
async def test_devolucion_fiado_abona_al_credito(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        cid = await _cliente(s)
        pid = await _producto(s, precio="20000", costo="12000", stock="100")
        await s.commit()
        svc = VentaService(SqlVentasRepository(s), fiados=FiadosService(SqlFiadosRepository(s)))
        venta = (await svc.registrar_venta(_venta(pid, "3", metodo="fiado", cliente_id=cid), vendedor_id=uid)).venta
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        saldo = (await s.execute(text("SELECT saldo_fiado FROM clientes WHERE id=:c"), {"c": cid})).scalar_one()
        assert saldo == Decimal("60000.00")   # deuda inicial

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).devolver(DevolucionCrear(venta_id=venta.id), usuario_id=uid)
        await s.commit()
        assert res.devolucion.metodo_reintegro == "fiado"

    async with AsyncSession(tenant.engine) as s:
        saldo = (await s.execute(text("SELECT saldo_fiado FROM clientes WHERE id=:c"), {"c": cid})).scalar_one()
        assert saldo == Decimal("0.00")   # la devolución abonó la deuda
        # No hay egreso de caja: el reintegro fue por crédito.
        assert (await s.execute(text("SELECT count(*) FROM caja_movimientos WHERE tipo='egreso'"))).scalar_one() == 0
    assert await _stock(tenant.engine, pid) == Decimal("100.000")


# --- devolución parcial ------------------------------------------------------
async def test_devolucion_parcial_solo_lo_pedido(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s, precio="20000", costo="12000", stock="100")
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "5"), vendedor_id=uid)).venta
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        res = await _svc(s).devolver(
            DevolucionCrear(
                venta_id=venta.id,
                lineas=[DevolucionLineaCrear(producto_id=pid, cantidad=Decimal("2"))],
            ),
            usuario_id=uid,
        )
        await s.commit()
        assert res.devolucion.total == Decimal("40000.00")   # 2 × 20000

    assert await _stock(tenant.engine, pid) == Decimal("97.000")   # 95 (tras venta) + 2


# --- INVARIANTE: aislamiento multi-tenant ------------------------------------
async def test_aislamiento_multitenant(tenant_factory):
    a = await tenant_factory()
    b = await tenant_factory()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        pid = await _producto(s, stock="100")
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        venta = (await VentaService(SqlVentasRepository(s)).registrar_venta(_venta(pid, "3"), vendedor_id=uid)).venta
        await s.commit()
    async with AsyncSession(a.engine, expire_on_commit=False) as s:
        await _svc(s).devolver(DevolucionCrear(venta_id=venta.id), usuario_id=uid)
        await s.commit()

    async with AsyncSession(a.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM devoluciones"))).scalar_one() == 1
    async with AsyncSession(b.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM devoluciones"))).scalar_one() == 0
        assert (
            await s.execute(text("SELECT count(*) FROM movimientos_inventario WHERE tipo='DEVOLUCION'"))
        ).scalar_one() == 0
