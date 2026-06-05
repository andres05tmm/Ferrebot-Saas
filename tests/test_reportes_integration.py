"""Integración del repositorio de reportes contra una base efímera real (Postgres).

Verifica el comportamiento SQL que los tests con fakes no pueden cubrir: excluir anuladas, agregar
por método de pago y respetar el scoping por vendedor. Inserta ventas directamente (SQL de prueba)
para fijar totales/estado/fecha de forma determinista.
"""
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co, rango_dia_co, today_co
from modules.reportes.repository import SqlReportesRepository


async def _usuario(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES (:n,'vendedor') RETURNING id"),
            {"n": nombre},
        )
    ).scalar_one()


async def _venta(
    s: AsyncSession, *, consecutivo: int, vendedor_id: int, total: str, metodo: str,
    estado: str = "completada",
) -> None:
    await s.execute(
        text(
            "INSERT INTO ventas "
            "(consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago, estado, origen) "
            "VALUES (:c, :v, :f, :t, 0, :t, :m, :e, 'web')"
        ),
        {"c": consecutivo, "v": vendedor_id, "f": now_co(), "t": total, "m": metodo, "e": estado},
    )


async def test_resumen_excluye_anuladas_y_respeta_scoping(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        a = await _usuario(s, "Ana")
        b = await _usuario(s, "Beto")
        await _venta(s, consecutivo=1, vendedor_id=a, total="20000.00", metodo="efectivo")
        await _venta(s, consecutivo=2, vendedor_id=a, total="10000.00", metodo="nequi")
        await _venta(s, consecutivo=3, vendedor_id=a, total="5000.00", metodo="efectivo", estado="anulada")
        await _venta(s, consecutivo=4, vendedor_id=b, total="99999.00", metodo="efectivo")
        await s.commit()

    inicio, fin = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine) as s:
        repo = SqlReportesRepository(s)
        solo_a = await repo.resumen(inicio=inicio, fin=fin, vendedor_id=a)
        todas = await repo.resumen(inicio=inicio, fin=fin, vendedor_id=None)
        sin_ventas = await repo.resumen(inicio=inicio, fin=fin, vendedor_id=a + b + 100)

    # Scoped a Ana: 2 ventas (la anulada NO cuenta), desglose por método.
    assert solo_a.num_ventas == 2
    assert solo_a.total_vendido == Decimal("30000.00")
    assert solo_a.por_metodo_pago == {"efectivo": Decimal("20000.00"), "nequi": Decimal("10000.00")}

    # Todas (None): Ana (2) + Beto (1); la anulada sigue excluida; efectivo se suma cross-vendedor.
    assert todas.num_ventas == 3
    assert todas.total_vendido == Decimal("129999.00")
    assert todas.por_metodo_pago == {
        "efectivo": Decimal("119999.00"), "nequi": Decimal("10000.00"),
    }

    # Vendedor sin ventas → ceros.
    assert sin_ventas.num_ventas == 0
    assert sin_ventas.total_vendido == Decimal("0")
    assert sin_ventas.por_metodo_pago == {}
