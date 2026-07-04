"""El COGS del P&L se ancla a la fecha de la VENTA origen, no al `creado_en` del movimiento (ADR 0025).

Antes, `estado_resultados` sumaba el costo de ventas por `MovimientoInventario.creado_en`, mientras
los ingresos iban por `Venta.fecha`. Al editar una venta de hoy, su SALIDA se re-crea con un
`creado_en` nuevo → ingreso y costo caían en días distintos. Ahora se filtra por
`coalesce(fecha_operacion, creado_en)` (la 0029 snapshotea la fecha de la venta en la SALIDA).
"""
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co, rango_dia_co, today_co
from modules.reportes.repository import SqlReportesRepository


async def _producto(s: AsyncSession) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES ('Cemento','unidad',1000,19,false,true) RETURNING id"
            )
        )
    ).scalar_one()


async def _salida(s: AsyncSession, *, producto_id: int, cantidad: str, costo: str, creado_en, fecha_operacion) -> None:
    await s.execute(
        text(
            "INSERT INTO movimientos_inventario (producto_id, tipo, cantidad, costo_unitario, creado_en, fecha_operacion) "
            "VALUES (:p, 'SALIDA', :cant, :costo, :ce, :fo)"
        ),
        {"p": producto_id, "cant": cantidad, "costo": costo, "ce": creado_en, "fo": fecha_operacion},
    )


async def test_cogs_cuenta_por_fecha_operacion_no_por_creado_en(tenant):
    hoy = now_co()
    mes_pasado = hoy - timedelta(days=40)
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s)
        # Cuenta HOY: se insertó el mes pasado pero su operación (venta) es de hoy.
        await _salida(s, producto_id=pid, cantidad="2", costo="5000", creado_en=mes_pasado, fecha_operacion=hoy)
        # NO cuenta hoy: se insertó hoy pero la operación fue el mes pasado (p. ej. edición tardía).
        await _salida(s, producto_id=pid, cantidad="10", costo="9999", creado_en=hoy, fecha_operacion=mes_pasado)
        await s.commit()

    inicio, fin = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine) as s:
        agg = await SqlReportesRepository(s).estado_resultados(inicio=inicio, fin=fin)

    assert agg.costo_ventas == Decimal("10000.00")   # 2·5000; la SALIDA anclada al mes pasado queda fuera


async def test_cogs_cae_a_creado_en_si_fecha_operacion_es_null(tenant):
    """Robustez de la migración: un movimiento previo a la 0029 (fecha_operacion NULL) cuenta por creado_en."""
    hoy = now_co()
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        pid = await _producto(s)
        await _salida(s, producto_id=pid, cantidad="1", costo="7000", creado_en=hoy, fecha_operacion=None)
        await s.commit()

    inicio, fin = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine) as s:
        agg = await SqlReportesRepository(s).estado_resultados(inicio=inicio, fin=fin)

    assert agg.costo_ventas == Decimal("7000.00")
