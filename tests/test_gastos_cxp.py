"""Gastos ↔ cuentas por pagar (ADR 0028): un gasto salda una factura SIN duplicar el abono.

Invariante crítico: registrar un gasto vinculado a una factura de proveedor genera EXACTAMENTE UN
abono (reduce `pendiente` una sola vez); un replay idempotente NO crea un segundo abono. El gasto ya
postea su egreso de caja (otro libro): son dos libros del mismo pago, no un doble cobro.
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import today_co
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.proveedores.errors import AbonoInvalido, FacturaProveedorInexistente
from modules.proveedores.repository import SqlProveedoresRepository


def _svc(s: AsyncSession) -> CajaService:
    return CajaService(SqlCajaRepository(s), SqlProveedoresRepository(s))


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
    ).scalar_one()


async def _factura(s: AsyncSession, *, fid="F-1", total="100000") -> None:
    await s.execute(
        text(
            "INSERT INTO facturas_proveedores (id, proveedor, total, pagado, pendiente, estado, fecha) "
            "VALUES (:id,'Tornillos SA',:t,0,:t,'pendiente',:f)"
        ),
        {"id": fid, "t": total, "f": today_co()},
    )


async def _pendiente(engine, fid="F-1") -> Decimal:
    async with AsyncSession(engine) as s:
        return Decimal(
            (
                await s.execute(
                    text("SELECT pendiente FROM facturas_proveedores WHERE id=:id"), {"id": fid}
                )
            ).scalar_one()
        )


async def _n_abonos(engine, fid="F-1") -> int:
    async with AsyncSession(engine) as s:
        return (
            await s.execute(
                text("SELECT count(*) FROM facturas_abonos WHERE factura_id=:id"), {"id": fid}
            )
        ).scalar_one()


async def test_gasto_salda_factura_reduce_pendiente_una_vez(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _factura(s, total="100000")
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        res = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("40000"), concepto="pago proveedor",
            factura_proveedor_id="F-1",
        )
        await s.commit()
        assert res.gasto.factura_proveedor_id == "F-1"
        assert res.gasto.abono_proveedor_id is not None

    assert await _pendiente(tenant.engine) == Decimal("60000.00")
    assert await _n_abonos(tenant.engine) == 1
    # El gasto también posteó su egreso de caja (libro de caja), independiente del abono.
    async with AsyncSession(tenant.engine) as s:
        egresos = (
            await s.execute(text("SELECT count(*) FROM caja_movimientos WHERE tipo='egreso'"))
        ).scalar_one()
    assert egresos == 1


async def test_replay_idempotente_no_duplica_abono(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _factura(s, total="100000")
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        r1 = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("40000"), concepto="pago",
            factura_proveedor_id="F-1", idempotency_key="g-1",
        )
        await s.commit()
        gid = r1.gasto.id

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        r2 = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="otros", monto=Decimal("40000"), concepto="pago",
            factura_proveedor_id="F-1", idempotency_key="g-1",
        )
        await s.commit()

    assert r2.replay is True and r2.gasto.id == gid
    assert await _n_abonos(tenant.engine) == 1                    # el replay NO creó un segundo abono
    assert await _pendiente(tenant.engine) == Decimal("60000.00")  # ni redujo el pendiente de nuevo


async def test_gasto_excede_pendiente_falla(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _factura(s, total="30000")
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        await s.commit()                       # el seed queda firme (el fallo no debe borrarlo)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(AbonoInvalido):
            await _svc(s).registrar_gasto(
                usuario_id=uid, categoria="otros", monto=Decimal("40000"), concepto="x",
                factura_proveedor_id="F-1",
            )
        await s.rollback()
    # Nada se movió: sin abono y con el pendiente intacto.
    assert await _n_abonos(tenant.engine) == 0
    assert await _pendiente(tenant.engine) == Decimal("30000.00")


async def test_gasto_a_factura_inexistente_falla(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        with pytest.raises(FacturaProveedorInexistente):
            await _svc(s).registrar_gasto(
                usuario_id=uid, categoria="otros", monto=Decimal("1000"), concepto="x",
                factura_proveedor_id="NOPE",
            )
        await s.rollback()


async def test_gasto_simple_sin_vinculo_sigue_funcionando(tenant):
    """Regresión: el gasto sin vínculo a CxP no toca proveedores (compat con el bot)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _svc(s).abrir(usuario_id=uid, saldo_inicial=Decimal("0"))
        res = await _svc(s).registrar_gasto(
            usuario_id=uid, categoria="transporte", monto=Decimal("15000"), concepto="taxi",
        )
        await s.commit()
    assert res.gasto.factura_proveedor_id is None and res.gasto.abono_proveedor_id is None
