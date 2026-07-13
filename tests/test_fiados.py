"""Integración de fiados: sobre-abono rechazado e idempotencia de cargo y abono."""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.fiados.errors import SobreAbono
from modules.fiados.repository import SqlFiadosRepository
from modules.fiados.service import FiadosService


async def _cliente(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO clientes (nombre, saldo_fiado) VALUES ('Cliente', 0) RETURNING id"))
    ).scalar_one()


def _svc(s):
    return FiadosService(SqlFiadosRepository(s))


async def test_sobre_abono_rechazado(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        await s.commit()
        fiado = (await _svc(s).crear(cliente_id=cid, venta_id=None, monto=Decimal("5000"))).fiado
        await s.commit()
        fid = fiado.id
        with pytest.raises(SobreAbono):
            await _svc(s).abonar(fiado_id=fid, monto=Decimal("6000"))
        await s.rollback()

    async with AsyncSession(tenant.engine) as s:
        # No se registró el abono; saldo y contadores intactos.
        abonos = (await s.execute(text("SELECT count(*) FROM fiados_movimientos WHERE tipo='abono'"))).scalar_one()
        assert abonos == 0
        saldo = (await s.execute(text("SELECT saldo FROM fiados WHERE id=:f"), {"f": fid})).scalar_one()
        assert saldo == Decimal("5000.00")
        cont = (await s.execute(text("SELECT saldo_fiado FROM clientes WHERE id=:c"), {"c": cid})).scalar_one()
        assert cont == Decimal("5000.00")


async def test_crear_fiado_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        await s.commit()
        r1 = await _svc(s).crear(cliente_id=cid, venta_id=None, monto=Decimal("8000"), idempotency_key="f1")
        await s.commit()
        fid = r1.fiado.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).crear(cliente_id=cid, venta_id=None, monto=Decimal("8000"), idempotency_key="f1")
        await s.commit()

    assert r2.replay is True and r2.fiado.id == fid
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM fiados"))).scalar_one() == 1
        cont = (await s.execute(text("SELECT saldo_fiado FROM clientes WHERE id=:c"), {"c": cid})).scalar_one()
        assert cont == Decimal("8000.00")   # cargado una sola vez


async def test_abono_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        await s.commit()
        fiado = (await _svc(s).crear(cliente_id=cid, venta_id=None, monto=Decimal("10000"))).fiado
        await s.commit()
        fid = fiado.id
        r1 = await _svc(s).abonar(fiado_id=fid, monto=Decimal("3000"), idempotency_key="a1")
        await s.commit()
        mid = r1.movimiento.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).abonar(fiado_id=fid, monto=Decimal("3000"), idempotency_key="a1")
        await s.commit()

    assert r2.replay is True and r2.movimiento.id == mid
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM fiados_movimientos WHERE tipo='abono'"))).scalar_one() == 1
        saldo = (await s.execute(text("SELECT saldo FROM fiados WHERE id=:f"), {"f": fid})).scalar_one()
        assert saldo == Decimal("7000.00")   # abonado una sola vez (10000 − 3000)


# ---- Listado por cliente (F2.3): alimenta el modal de abono del dashboard ----
async def test_fiados_de_cliente_solo_con_saldo_y_viejos_primero(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        cid = await _cliente(s)
        otro = await _cliente(s)
        await s.commit()
        viejo = (await _svc(s).crear(cliente_id=cid, venta_id=None, monto=Decimal("5000"))).fiado
        nuevo = (await _svc(s).crear(cliente_id=cid, venta_id=None, monto=Decimal("8000"))).fiado
        pagado = (await _svc(s).crear(cliente_id=cid, venta_id=None, monto=Decimal("2000"))).fiado
        ajeno = (await _svc(s).crear(cliente_id=otro, venta_id=None, monto=Decimal("999"))).fiado
        await s.commit()
        await _svc(s).abonar(fiado_id=pagado.id, monto=Decimal("2000"))   # saldo 0 → fuera
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        fiados = await _svc(s).fiados_de(cid)
    ids = [f.id for f in fiados]
    assert ids == [viejo.id, nuevo.id]          # viejos primero; el saldado no aparece
    assert ajeno.id not in ids                  # solo los del cliente pedido
    # Cliente sin fiados → lista vacía (sin 404: es una lectura de modal).
    async with AsyncSession(tenant.engine) as s:
        assert await _svc(s).fiados_de(999999) == []
