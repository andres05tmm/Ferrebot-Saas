"""Los días ANTERIORES al sistema: totales anotados a mano, aislados de la contabilidad.

De esos días el dueño solo tiene el total del cuaderno: no hay gastos, ni caja, ni movimientos de
inventario que los acompañen. Por eso se guardan con `incluir_en_balances=False` y NO entran a
ningún reporte financiero. Ese aislamiento es lo que más fácil se rompe dentro de seis meses cuando
alguien quiera "que cuadre", así que tiene test propio.
"""
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import rango_dia_co, today_co
from modules.reportes.repository import SqlReportesRepository
from modules.ventas.repository import SqlVentasRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService

_VIEJO = date(2026, 3, 15)
_OTRO = date(2026, 3, 16)


async def test_se_guardan_los_dias_del_cuaderno(tenant):
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlReportesRepository(s)
        assert await repo.cargar_historico([(_VIEJO, Decimal("1030500")), (_OTRO, Decimal("898500"))]) == 2
        await s.commit()

        guardados = await repo.historico_por_dia(desde=_VIEJO, hasta=_OTRO)

    assert guardados == {_VIEJO: Decimal("1030500.00"), _OTRO: Decimal("898500.00")}


async def test_pegar_la_lista_dos_veces_corrige_en_vez_de_duplicar(tenant):
    """El caso real: el dueño pega, ve un número mal tecleado y vuelve a pegar la lista corregida.
    `fecha` es PK, así que el UPSERT lo vuelve repetible sin pensar."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlReportesRepository(s)
        await repo.cargar_historico([(_VIEJO, Decimal("1000000"))])
        await s.commit()
        await repo.cargar_historico([(_VIEJO, Decimal("1030500"))])
        await s.commit()

        n = (await s.execute(text("SELECT count(*) FROM historico_ventas"))).scalar_one()
        guardados = await repo.historico_por_dia(desde=_VIEJO, hasta=_VIEJO)

    assert n == 1
    assert guardados[_VIEJO] == Decimal("1030500.00")


async def test_se_marcan_como_fuera_de_los_balances(tenant):
    """El flag es la frontera. Sin él, cualquier consulta futura que lea la tabla los sumaría."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await SqlReportesRepository(s).cargar_historico([(_VIEJO, Decimal("500000"))])
        await s.commit()

        fila = (
            await s.execute(text("SELECT origen, incluir_en_balances FROM historico_ventas"))
        ).one()

    assert fila.origen == "manual"
    assert fila.incluir_en_balances is False


async def test_ningun_reporte_financiero_los_suma(tenant):
    """EL invariante de esta feature.

    Se carga un día viejo con un millón y se comprueba que el estado de resultados, el resumen y los
    totales de ventas siguen en cero. De esos días no se anotaron gastos: contarlos como ingreso
    daría una utilidad que nunca existió.
    """
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        repo = SqlReportesRepository(s)
        await repo.cargar_historico([(_VIEJO, Decimal("1000000"))])
        await s.commit()

        inicio, fin = rango_dia_co(_VIEJO, _VIEJO)
        resultados = await repo.estado_resultados(inicio=inicio, fin=fin)
        resumen = await repo.resumen(inicio=inicio, fin=fin, vendedor_id=None)
        total = await repo.total_ventas(inicio=inicio, fin=fin, vendedor_id=None)

    assert resultados.ingresos == Decimal("0")
    assert resumen.total_vendido == Decimal("0")
    assert total == Decimal("0")


async def test_el_calendario_los_muestra_en_su_propio_campo(tenant):
    """En el calendario SÍ aparecen —para eso se cargaron— pero nunca sumados a `total`: si se
    mezclaran, el mes mostraría una cifra que ningún reporte puede respaldar y nadie sabría cuál de
    los dos números está mal."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        await SqlReportesRepository(s).cargar_historico([(_VIEJO, Decimal("1030500"))])
        await s.commit()

        inicio, fin = rango_dia_co(date(2026, 3, 1), date(2026, 3, 31))
        dias = await SqlReportesRepository(s).calendario(inicio=inicio, fin=fin, vendedor_id=None)

    dia = next(d for d in dias if d.fecha == _VIEJO)
    assert dia.historico == Decimal("1030500.00")
    assert dia.total == Decimal("0")            # no hubo ventas del sistema ese día
    assert dia.num_ventas == 0


async def test_un_dia_del_sistema_conserva_su_total_aunque_tenga_historico(tenant):
    """No deberían solaparse (el histórico es de antes del sistema), pero si pasa, los dos números
    se ven por separado en vez de sumarse en silencio."""
    async with AsyncSession(tenant.engine, expire_on_commit=False) as s:
        uid = (
            await s.execute(text("INSERT INTO usuarios (nombre, rol) VALUES ('V','vendedor') RETURNING id"))
        ).scalar_one()
        pid = (
            await s.execute(text(
                "INSERT INTO productos (nombre, unidad_medida, precio_venta, iva, permite_fraccion, activo) "
                "VALUES ('Martillo','unidad',10000,0,false,true) RETURNING id"))
        ).scalar_one()
        await s.execute(text("INSERT INTO inventario (producto_id, stock_actual, stock_minimo) "
                             "VALUES (:p,100,0)"), {"p": pid})
        await s.commit()
        await VentaService(SqlVentasRepository(s)).registrar_venta(
            VentaCrear(metodo_pago="efectivo",
                       lineas=[VentaDetalleCrear(producto_id=pid, cantidad=Decimal("1"))]),
            vendedor_id=uid,
        )
        hoy = today_co()
        await SqlReportesRepository(s).cargar_historico([(hoy, Decimal("777"))])
        await s.commit()

        inicio, fin = rango_dia_co(hoy, hoy)
        dias = await SqlReportesRepository(s).calendario(inicio=inicio, fin=fin, vendedor_id=None)

    dia = next(d for d in dias if d.fecha == hoy)
    assert dia.total == Decimal("10000.00")     # lo que registró el sistema
    assert dia.historico == Decimal("777.00")   # lo anotado a mano, aparte
