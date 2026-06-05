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
from modules.reportes.service import ReportesService


async def _usuario(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(
            text("INSERT INTO usuarios (nombre, rol) VALUES (:n,'vendedor') RETURNING id"),
            {"n": nombre},
        )
    ).scalar_one()


async def _producto(s: AsyncSession, nombre: str) -> int:
    return (
        await s.execute(
            text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES (:n,'unidad',1000,19,false,true) RETURNING id"
            ),
            {"n": nombre},
        )
    ).scalar_one()


async def _venta_sub(
    s: AsyncSession, *, consecutivo: int, vendedor_id: int, subtotal: str, total: str,
    estado: str = "completada", metodo: str = "efectivo",
) -> int:
    """Inserta una venta con subtotal distinto del total (ingresos = Σ subtotal) y devuelve su id."""
    return (
        await s.execute(
            text(
                "INSERT INTO ventas "
                "(consecutivo, vendedor_id, fecha, subtotal, impuestos, total, metodo_pago, estado, origen) "
                "VALUES (:c,:v,:f,:s,0,:t,:m,:e,'web') RETURNING id"
            ),
            {"c": consecutivo, "v": vendedor_id, "f": now_co(), "s": subtotal, "t": total, "m": metodo, "e": estado},
        )
    ).scalar_one()


async def _detalle(
    s: AsyncSession, *, venta_id: int, producto_id: int | None, cantidad: str, precio_unitario: str,
) -> None:
    await s.execute(
        text(
            "INSERT INTO ventas_detalle (venta_id, producto_id, cantidad, precio_unitario, iva) "
            "VALUES (:vid, :pid, :cant, :pu, 19)"
        ),
        {"vid": venta_id, "pid": producto_id, "cant": cantidad, "pu": precio_unitario},
    )


async def _salida(s: AsyncSession, *, producto_id: int, cantidad: str, costo: str | None) -> None:
    await s.execute(
        text(
            "INSERT INTO movimientos_inventario (producto_id, tipo, cantidad, costo_unitario, creado_en) "
            "VALUES (:p, 'SALIDA', :cant, :costo, :f)"
        ),
        {"p": producto_id, "cant": cantidad, "costo": costo, "f": now_co()},
    )


async def _gasto(s: AsyncSession, monto: str) -> None:
    await s.execute(
        text("INSERT INTO gastos (categoria, monto, creado_en) VALUES ('otros', :m, :f)"),
        {"m": monto, "f": now_co()},
    )


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


async def test_resultados_cuadran_y_excluyen_anuladas(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        a = await _usuario(s, "Ana")
        p1 = await _producto(s, "Cemento")
        # Ingresos = Σ subtotal de completadas; la anulada NO suma.
        await _venta_sub(s, consecutivo=1, vendedor_id=a, subtotal="20000.00", total="23800.00")
        await _venta_sub(s, consecutivo=2, vendedor_id=a, subtotal="10000.00", total="11900.00")
        await _venta_sub(s, consecutivo=3, vendedor_id=a, subtotal="99999.00", total="99999.00", estado="anulada")
        # Costo de ventas = Σ(costo × cantidad) de SALIDA; el costo NULL cuenta 0.
        await _salida(s, producto_id=p1, cantidad="2", costo="5000")   # 10000
        await _salida(s, producto_id=p1, cantidad="1", costo="8000")   #  8000
        await _salida(s, producto_id=p1, cantidad="5", costo=None)     #     0
        # Gastos del rango.
        await _gasto(s, "5000")
        await _gasto(s, "3000")
        await s.commit()

    async with AsyncSession(tenant.engine) as s:
        agg = await SqlReportesRepository(s).estado_resultados(
            inicio=rango_dia_co(today_co(), today_co())[0],
            fin=rango_dia_co(today_co(), today_co())[1],
        )
        out = await ReportesService(SqlReportesRepository(s)).estado_resultados(
            desde=today_co(), hasta=today_co()
        )

    assert agg.ingresos == Decimal("30000.00")        # anulada excluida
    assert agg.costo_ventas == Decimal("18000.00")    # 10000 + 8000 + 0
    assert agg.gastos == Decimal("8000.00")
    assert out.utilidad_bruta == Decimal("12000.00")  # 30000 − 18000
    assert out.utilidad_neta == Decimal("4000.00")    # 12000 − 8000


async def test_top_productos_ranking_scoping_excluye_anuladas_y_varia(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        a = await _usuario(s, "Ana")
        b = await _usuario(s, "Beto")
        p1 = await _producto(s, "Cemento")
        p2 = await _producto(s, "Arena")
        v1 = await _venta_sub(s, consecutivo=1, vendedor_id=a, subtotal="25000", total="25000")
        await _detalle(s, venta_id=v1, producto_id=p1, cantidad="2", precio_unitario="10000")  # 20000
        await _detalle(s, venta_id=v1, producto_id=p2, cantidad="1", precio_unitario="5000")   #  5000
        v2 = await _venta_sub(s, consecutivo=2, vendedor_id=a, subtotal="15000", total="15000")
        await _detalle(s, venta_id=v2, producto_id=p1, cantidad="1", precio_unitario="10000")  # 10000
        await _detalle(s, venta_id=v2, producto_id=None, cantidad="1", precio_unitario="9999") # varia (excluida)
        v3 = await _venta_sub(s, consecutivo=3, vendedor_id=b, subtotal="15000", total="15000")
        await _detalle(s, venta_id=v3, producto_id=p2, cantidad="3", precio_unitario="5000")   # 15000
        v4 = await _venta_sub(s, consecutivo=4, vendedor_id=a, subtotal="999999", total="999999", estado="anulada")
        await _detalle(s, venta_id=v4, producto_id=p1, cantidad="100", precio_unitario="10000")  # anulada (excluida)
        await s.commit()

    inicio, fin = rango_dia_co(today_co(), today_co())
    async with AsyncSession(tenant.engine) as s:
        repo = SqlReportesRepository(s)
        todos = await repo.top_productos(inicio=inicio, fin=fin, vendedor_id=None, limite=10)
        solo_a = await repo.top_productos(inicio=inicio, fin=fin, vendedor_id=a, limite=10)

    # Negocio completo: p1 = 20000+10000 = 30000 (anulada fuera); p2 = 5000+15000 = 20000. Orden ingreso desc.
    assert [f.producto_id for f in todos] == [p1, p2]
    por_id = {f.producto_id: f for f in todos}
    assert por_id[p1].ingreso == Decimal("30000.00")
    assert por_id[p1].cantidad == Decimal("3.000")
    assert por_id[p2].ingreso == Decimal("20000.00")
    assert None not in por_id                          # la varia no aparece

    # Scoped a Ana: p1 30000, p2 5000 (sin los de Beto).
    a_por_id = {f.producto_id: f for f in solo_a}
    assert [f.producto_id for f in solo_a] == [p1, p2]
    assert a_por_id[p1].ingreso == Decimal("30000.00")
    assert a_por_id[p2].ingreso == Decimal("5000.00")
