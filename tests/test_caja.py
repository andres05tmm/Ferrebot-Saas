"""Integración de caja/gastos contra base efímera: apertura idempotente, lock e idempotencia."""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('Vendedor','vendedor') RETURNING id"))
    ).scalar_one()


def _svc(s):
    return CajaService(SqlCajaRepository(s))


async def test_apertura_idempotente_una_sola_caja(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        r1 = await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("100000"))
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("999999"))
        await s.commit()

    assert r1.replay is False and r2.replay is True
    assert r2.caja.id == r1.caja.id
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM caja"))).scalar_one() == 1


async def test_movimiento_sin_caja_abierta_falla(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        with pytest.raises(CajaNoAbierta):
            await _svc(s).registrar_movimiento(
                usuario_id=uid, tipo="ingreso", monto=Decimal("1000"), concepto="x"
            )
        await s.rollback()


async def test_movimiento_idempotente(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        r1 = await _svc(s).registrar_movimiento(
            usuario_id=uid, tipo="ingreso", monto=Decimal("5000"), concepto="ajuste", idempotency_key="m1"
        )
        await s.commit()
        mid = r1.movimiento.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).registrar_movimiento(
            usuario_id=uid, tipo="ingreso", monto=Decimal("5000"), concepto="ajuste", idempotency_key="m1"
        )
        await s.commit()

    assert r2.replay is True
    assert r2.movimiento.id == mid
    async with AsyncSession(tenant.engine) as s:
        assert (await s.execute(text("SELECT count(*) FROM caja_movimientos"))).scalar_one() == 1
