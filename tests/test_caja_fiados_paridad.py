"""Paridad en BD: gasto→caja (una vez), anti-doble-conteo en el cierre y consistencia del ledger."""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id"))
    ).scalar_one()


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Cliente', 0) RETURNING id"))
    ).scalar_one()


async def _venta_efectivo(s: AsyncSession, *, vendedor_id: int, total: str) -> None:
    await s.execute(
        text(
            "INSERT INTO ventas (consecutivo, vendedor_id, fecha, subtotal, impuestos, total, "
            "metodo_pago, estado, origen) VALUES "
            "(nextval('ventas_consecutivo_seq'), :v, :f, :t, 0, :t, 'efectivo', 'completada', 'web')"
        ),
        {"v": vendedor_id, "f": now_co(), "t": total},
    )


def _caja(s):
    return CajaService(SqlCajaRepository(s))


# ---- Brecha §6/§8: el gasto mueve caja, exactamente una vez -------------------
async def test_gasto_mueve_caja_exactamente_un_egreso(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        await _caja(s).abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        res = await _caja(s).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("40000"), concepto="taxi"
        )
        await s.commit()
        gasto_id = res.gasto.id

    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM gastos"))).scalar_one() == 1
        # EXACTAMENTE 1 egreso ligado a ese gasto (no 0, no 2).
        rows = (
            await s.execute(
                text("SELECT tipo, monto FROM caja_movimientos WHERE referencia = :r"),
                {"r": f"gasto:{gasto_id}"},
            )
        ).all()
        assert len(rows) == 1
        assert rows[0][0] == "egreso"
        assert rows[0][1] == Decimal("40000.00")


# ---- Guardrail anti-doble-conteo: el gasto baja el esperado UNA sola vez ------
async def test_gasto_se_cuenta_una_vez_en_el_cierre(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        await _caja(s).abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        await _caja(s).registrar_gasto(usuario_id=uid, categoria="otros", monto=Decimal("40000"), concepto="taxi")
        await s.commit()
        caja = await _caja(s).cerrar(usuario_id=uid, saldo_contado=Decimal("60000"))
        await s.commit()

    # esperado = 100000 + 0 ventas + 0 ingresos − 40000 egresos(gasto) = 60000 (baja exactamente 40000).
    # Si se contara doble (tabla gastos + egreso) sería 20000.
    assert caja.saldo_esperado == Decimal("60000.00")
    assert caja.diferencia == Decimal("0.00")


# ---- saldo_esperado híbrido: ventas efectivo (tabla ventas) + caja_movimientos --
async def test_cierre_suma_ventas_efectivo_y_resta_gastos(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        await _caja(s).abrir(usuario_id=uid, saldo_inicial=Decimal("50000"))
        await _venta_efectivo(s, vendedor_id=uid, total="200000")
        await _caja(s).registrar_gasto(usuario_id=uid, categoria="otros", monto=Decimal("30000"), concepto="x")
        await s.commit()
        caja = await _caja(s).cerrar(usuario_id=uid, saldo_contado=Decimal("220000"))
        await s.commit()

    assert caja.saldo_esperado == Decimal("220000.00")   # 50000 + 200000 − 30000
    assert caja.diferencia == Decimal("0.00")


# ---- Consistencia del ledger: fiados_movimientos == contadores, por fiado y cliente --
async def test_consistencia_ledger_por_fiado_y_cliente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        await s.commit()
        fsvc = FiadosService(SqlFiadosRepository(s))
        f1 = (await fsvc.crear(cliente_id=cid, venta_id=None, monto=Decimal("20000"))).fiado
        f2 = (await fsvc.crear(cliente_id=cid, venta_id=None, monto=Decimal("10000"))).fiado
        await s.commit()
        f1_id, f2_id = f1.id, f2.id
        await fsvc.abonar(fiado_id=f1_id, monto=Decimal("5000"))
        await fsvc.abonar(fiado_id=f2_id, monto=Decimal("8000"))
        await s.commit()

    _ledger_fiado = (
        "SELECT COALESCE(SUM(CASE WHEN tipo='cargo' THEN monto ELSE -monto END), 0) "
        "FROM fiados_movimientos WHERE fiado_id = :f"
    )
    async with AsyncSession(tenant.engine) as s:
        # Por fiado: fiados.saldo == Σ(cargo − abono) de SUS movimientos.
        for fid, esperado in ((f1_id, "15000.00"), (f2_id, "2000.00")):
            ledger = (await s.execute(text(_ledger_fiado), {"f": fid})).scalar_one()
            saldo = (await s.execute(text("SELECT saldo FROM fiados WHERE id = :f"), {"f": fid})).scalar_one()
            assert saldo == ledger == Decimal(esperado)

        # Por cliente: clientes.saldo_fiado == Σ ledger de todos sus fiados == Σ fiados.saldo.
        ledger_cliente = (
            await s.execute(
                text(
                    "SELECT COALESCE(SUM(CASE WHEN m.tipo='cargo' THEN m.monto ELSE -m.monto END), 0) "
                    "FROM fiados_movimientos m JOIN fiados f ON f.id = m.fiado_id WHERE f.cliente_id = :c"
                ),
                {"c": cid},
            )
        ).scalar_one()
        suma_saldos = (
            await s.execute(text("SELECT COALESCE(SUM(saldo), 0) FROM fiados WHERE cliente_id = :c"), {"c": cid})
        ).scalar_one()
        contador = (
            await s.execute(text("SELECT saldo_fiado FROM clientes WHERE id = :c"), {"c": cid})
        ).scalar_one()
        assert contador == ledger_cliente == suma_saldos == Decimal("17000.00")   # 15000 + 2000
