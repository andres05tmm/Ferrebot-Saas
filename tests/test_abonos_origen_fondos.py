"""Abono a proveedor: de dónde sale la plata (TDD-primero).

Hasta hoy un abono NUNCA movía la caja: si el dueño le pagaba al proveedor con el efectivo del
cajón, el arqueo quedaba descuadrado y el egreso no existía en ningún libro. Ahora el abono declara
su origen:

- `caja` → postea su egreso (referencia `abono:{id}`, para el reporte de egresos por procedencia) y
  lo enlaza en la fila del abono;
- `efectivo_externo` (plata guardada de días anteriores) y `banco` → bajan la deuda y registran el
  medio, pero NO tocan el cajón del día.

Integración contra base efímera real (fixture `tenant`).
"""
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import today_co
from modules.caja.errors import CajaNoAbierta
from modules.caja.repository import SqlCajaRepository
from modules.caja.service import CajaService
from modules.proveedores.repository import SqlProveedoresRepository
from modules.proveedores.schemas import AbonoCrear, FacturaProveedorCrear
from modules.proveedores.service import ProveedoresService


def _svc(s: AsyncSession) -> ProveedoresService:
    return ProveedoresService(SqlProveedoresRepository(s), caja=CajaService(SqlCajaRepository(s)))


async def _usuario(s: AsyncSession) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES ('Dueño','admin') RETURNING id")
        )
    ).scalar_one()


async def _factura(s: AsyncSession, *, total="100000") -> str:
    await _svc(s).crear_factura(
        FacturaProveedorCrear(
            id="F-1", proveedor="Ferrisariato", total=Decimal(total), fecha=today_co()
        ),
        usuario_id=None,
    )
    return "F-1"


async def _egresos(engine) -> list[tuple]:
    async with AsyncSession(engine) as s:
        return [
            tuple(f)
            for f in (
                await s.execute(
                    text(
                        "SELECT monto, referencia FROM caja_movimientos WHERE tipo='egreso' ORDER BY id"
                    )
                )
            ).all()
        ]


async def test_abono_desde_caja_postea_su_egreso_y_lo_enlaza(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("500000"))
        await _factura(s)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        factura = await _svc(s).registrar_abono(
            AbonoCrear(factura_id="F-1", monto=Decimal("40000"), origen_fondos="caja"),
            usuario_id=uid,
        )
        await s.commit()

    assert factura.pendiente == Decimal("60000.00")
    async with AsyncSession(tenant.engine) as s:
        abono_id, origen, movimiento_id = (
            await s.execute(
                text(
                    "SELECT id, origen_fondos, caja_movimiento_id FROM facturas_abonos "
                    "WHERE factura_id='F-1'"
                )
            )
        ).one()
    assert origen == "caja" and movimiento_id is not None
    assert await _egresos(tenant.engine) == [(Decimal("40000.00"), f"abono:{abono_id}")]


async def test_abono_por_fuera_de_la_caja_no_mueve_el_cajon(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await s.commit()
        await CajaService(SqlCajaRepository(s)).abrir(usuario_id=uid, saldo_inicial=Decimal("500000"))
        await _factura(s)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await _svc(s).registrar_abono(
            AbonoCrear(factura_id="F-1", monto=Decimal("40000"), origen_fondos="banco"),
            usuario_id=uid,
        )
        await s.commit()

    assert await _egresos(tenant.engine) == []
    async with AsyncSession(tenant.engine) as s:
        origen, movimiento_id = (
            await s.execute(
                text(
                    "SELECT origen_fondos, caja_movimiento_id FROM facturas_abonos "
                    "WHERE factura_id='F-1'"
                )
            )
        ).one()
    assert origen == "banco" and movimiento_id is None


async def test_abono_desde_caja_sin_caja_abierta_no_deja_abono(tenant):
    """Sin caja no hay de dónde pagar: falla y la deuda queda intacta (sin efectos parciales)."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = await _usuario(s)
        await _factura(s)
        await s.commit()

    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        with pytest.raises(CajaNoAbierta):
            await _svc(s).registrar_abono(
                AbonoCrear(factura_id="F-1", monto=Decimal("40000"), origen_fondos="caja"),
                usuario_id=uid,
            )
        await s.rollback()

    async with AsyncSession(tenant.engine) as s:
        abonos = (await s.execute(text("SELECT count(*) FROM facturas_abonos"))).scalar_one()
        pendiente = (
            await s.execute(text("SELECT pendiente FROM facturas_proveedores WHERE id='F-1'"))
        ).scalar_one()
    assert abonos == 0 and Decimal(pendiente) == Decimal("100000.00")
