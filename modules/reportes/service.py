"""Servicio de reportes: deriva el resumen del día desde el agregado del repositorio.

Lógica pura y testeable: depende del puerto `ReportesRepo` (falseado en tests). Calcula el ticket
promedio (Decimal, 0 si no hubo ventas) y fija la fecha del día en hora Colombia.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from core.config.timezone import rango_dia_co, today_co
from core.money import cuantizar
from modules.reportes.repository import (
    AgingProveedorFila,
    AgregadoDia,
    AgregadoFlujo,
    AgregadoLibroIVA,
    AgregadoResultados,
    DiaCalendario,
    MargenProductoFila,
    TopProductoFila,
)
from modules.reportes.schemas import (
    AgingProveedor,
    DiaCalendarioLeer,
    EstadoResultados,
    FlujoDinero,
    LibroIVA,
    MargenProducto,
    ProyeccionCaja,
    PuntoSerie,
    ResumenDia,
    TopProducto,
    TotalesVentas,
)


class ReportesRepo(Protocol):
    """Puerto de datos de reportes (lo implementa SqlReportesRepository; los tests lo falsean)."""

    async def resumen(self, *, inicio, fin, vendedor_id: int | None) -> AgregadoDia: ...
    async def estado_resultados(self, *, inicio, fin) -> AgregadoResultados: ...
    async def libro_iva(self, *, inicio, fin) -> AgregadoLibroIVA: ...
    async def serie_ventas(
        self, *, inicio, fin, vendedor_id: int | None
    ) -> list[tuple[date, Decimal]]: ...
    async def total_ventas(self, *, inicio, fin, vendedor_id: int | None) -> Decimal: ...
    async def top_productos(
        self, *, inicio, fin, vendedor_id: int | None, limite: int
    ) -> list[TopProductoFila]: ...
    async def flujo_dinero(self, *, inicio, fin) -> AgregadoFlujo: ...
    async def margen_productos(
        self, *, inicio, fin, por_categoria: bool, limite: int
    ) -> list[MargenProductoFila]: ...
    async def aging_cxp(self, *, hoy: date) -> list[AgingProveedorFila]: ...
    async def gastos_por_dia(self, *, inicio, fin) -> list[tuple[date, Decimal]]: ...
    async def calendario(
        self, *, inicio, fin, vendedor_id: int | None
    ) -> list[DiaCalendario]: ...


def _rango_o_mes(desde: date | None, hasta: date | None) -> tuple[date, date]:
    """Resuelve el rango: ausente → mes en curso (día 1 → hoy Colombia). Nunca date.today() crudo."""
    hoy = today_co()
    return (desde or hoy.replace(day=1)), (hasta or hoy)


def _promedio_dias_con_movimiento(serie: list[tuple[date, Decimal]]) -> Decimal:
    """Promedio diario sobre los días CON movimiento (>0): un negocio que cierra domingos no debe
    diluir su promedio con ceros (fórmula del /proyeccion del dashboard viejo). 0 si no hubo días."""
    con_mov = [t for _, t in serie if t > 0]
    if not con_mov:
        return Decimal("0")
    return sum(con_mov, Decimal("0")) / len(con_mov)


class ReportesService:
    def __init__(self, repo: ReportesRepo) -> None:
        self._repo = repo

    async def resumen_dia(self, vendedor_id: int | None) -> ResumenDia:
        """Resumen de HOY (Colombia): conteo, total, ticket promedio y desglose por método de pago."""
        hoy = today_co()
        inicio, fin = rango_dia_co(hoy, hoy)
        agg = await self._repo.resumen(inicio=inicio, fin=fin, vendedor_id=vendedor_id)
        ticket = (
            cuantizar(agg.total_vendido / agg.num_ventas) if agg.num_ventas else Decimal("0")
        )
        return ResumenDia(
            fecha=hoy,
            num_ventas=agg.num_ventas,
            total_vendido=agg.total_vendido,
            ticket_promedio=ticket,
            por_metodo_pago=agg.por_metodo_pago,
        )

    async def estado_resultados(
        self, *, desde: date | None, hasta: date | None
    ) -> EstadoResultados:
        """Estado de resultados del rango (default mes en curso): utilidad bruta y neta del negocio."""
        d, h = _rango_o_mes(desde, hasta)
        inicio, fin = rango_dia_co(d, h)
        agg = await self._repo.estado_resultados(inicio=inicio, fin=fin)
        utilidad_bruta = agg.ingresos - agg.costo_ventas
        utilidad_neta = utilidad_bruta - agg.gastos
        return EstadoResultados(
            desde=d, hasta=h,
            ingresos=agg.ingresos, costo_ventas=agg.costo_ventas,
            utilidad_bruta=utilidad_bruta, gastos=agg.gastos, utilidad_neta=utilidad_neta,
        )

    async def libro_iva(self, *, desde: date | None, hasta: date | None) -> LibroIVA:
        """Libro IVA del rango (default mes en curso): IVA generado vs descontable y su saldo.

        `saldo = iva_generado − iva_descontable` (positivo = a pagar; negativo = a favor). Solo cruza
        datos existentes (ventas + compras fiscales); no toca la DIAN.
        """
        d, h = _rango_o_mes(desde, hasta)
        inicio, fin = rango_dia_co(d, h)
        agg = await self._repo.libro_iva(inicio=inicio, fin=fin)
        saldo = agg.iva_generado - agg.iva_descontable
        return LibroIVA(
            desde=d, hasta=h,
            base_ventas=agg.base_ventas, iva_generado=agg.iva_generado,
            base_compras=agg.base_compras, iva_descontable=agg.iva_descontable,
            saldo=saldo,
        )

    async def serie_ventas(self, *, dias: int, vendedor_id: int | None) -> list[PuntoSerie]:
        """Serie diaria de los últimos `dias` (incluido hoy), hora Colombia, con los vacíos en 0.

        Para la gráfica de evolución y el sparkline del tab Hoy. Rellena TODOS los días del rango
        (aunque no haya ventas) para que la serie tenga puntos continuos.
        """
        hoy = today_co()
        desde = hoy - timedelta(days=dias - 1)
        inicio, fin = rango_dia_co(desde, hoy)
        por_dia = {
            f: t for f, t in await self._repo.serie_ventas(inicio=inicio, fin=fin, vendedor_id=vendedor_id)
        }
        serie: list[PuntoSerie] = []
        actual = desde
        while actual <= hoy:
            serie.append(PuntoSerie(fecha=actual, total=por_dia.get(actual, Decimal("0"))))
            actual += timedelta(days=1)
        return serie

    async def totales(self, *, vendedor_id: int | None) -> TotalesVentas:
        """Totales de ventas: hoy / últimos 7 días / mes en curso (hora Colombia), acotados al vendedor."""
        hoy = today_co()
        dia = await self._total(hoy, hoy, vendedor_id)
        semana = await self._total(hoy - timedelta(days=6), hoy, vendedor_id)
        mes = await self._total(hoy.replace(day=1), hoy, vendedor_id)
        return TotalesVentas(dia=dia, semana=semana, mes=mes)

    async def _total(self, desde: date, hasta: date, vendedor_id: int | None) -> Decimal:
        """Suma del total de ventas completadas del rango [desde, hasta] (hora Colombia)."""
        inicio, fin = rango_dia_co(desde, hasta)
        return await self._repo.total_ventas(inicio=inicio, fin=fin, vendedor_id=vendedor_id)

    async def flujo_dinero(self, *, desde: date | None, hasta: date | None) -> FlujoDinero:
        """Flujo de dinero simple del rango (default mes): entradas vs salidas y el neto.

        No exige el ledger contable: cruza las tablas operativas. Fiado excluido de entradas
        (es cartera); abonos de fiados incluidos; dedup del gasto→abono (ADR 0028) en el repo."""
        d, h = _rango_o_mes(desde, hasta)
        inicio, fin = rango_dia_co(d, h)
        agg = await self._repo.flujo_dinero(inicio=inicio, fin=fin)
        total_entradas = (
            sum(agg.ventas_por_metodo.values(), Decimal("0"))
            + agg.abonos_fiados + agg.ingresos_caja
        )
        total_salidas = (
            sum(agg.gastos_por_categoria.values(), Decimal("0"))
            + agg.abonos_proveedores + agg.egresos_caja
        )
        return FlujoDinero(
            desde=d, hasta=h,
            ventas_por_metodo=agg.ventas_por_metodo, ventas_fiado=agg.ventas_fiado,
            abonos_fiados=agg.abonos_fiados, ingresos_caja=agg.ingresos_caja,
            total_entradas=total_entradas,
            gastos_por_categoria=agg.gastos_por_categoria,
            abonos_proveedores=agg.abonos_proveedores, egresos_caja=agg.egresos_caja,
            total_salidas=total_salidas,
            neto=total_entradas - total_salidas,
        )

    async def margen_productos(
        self, *, desde: date | None, hasta: date | None, por: str, limite: int
    ) -> list[MargenProducto]:
        """Margen bruto por producto o categoría (default mes), con cobertura de costo honesta:
        `margen_pct` solo es confiable si `cobertura_pct` está cerca de 100 (lección del viejo:
        un CMV en $0 silencioso infla el margen)."""
        d, h = _rango_o_mes(desde, hasta)
        inicio, fin = rango_dia_co(d, h)
        filas = await self._repo.margen_productos(
            inicio=inicio, fin=fin, por_categoria=(por == "categoria"), limite=limite
        )
        salida = []
        for f in filas:
            margen = f.ingresos - f.cogs
            margen_pct = (
                cuantizar(margen / f.ingresos * 100) if f.ingresos > 0 else None
            )
            con_costo = f.cantidad - f.unidades_sin_costo
            cobertura = (
                cuantizar(con_costo / f.cantidad * 100) if f.cantidad > 0 else Decimal("0")
            )
            salida.append(MargenProducto(
                clave=f.clave, producto_id=f.producto_id, cantidad=f.cantidad,
                ingresos=f.ingresos, cogs=f.cogs, margen=margen,
                margen_pct=margen_pct, cobertura_pct=cobertura,
            ))
        return salida

    async def aging_cxp(self) -> list[AgingProveedor]:
        """Cartera por pagar por proveedor en tramos de antigüedad, con semáforo (verde ≤30 días,
        ámbar ≤60, rojo >60 — misma lógica visual del dashboard viejo)."""
        filas = await self._repo.aging_cxp(hoy=today_co())
        def _semaforo(f: AgingProveedorFila) -> str:
            if f.d61_90 > 0 or f.d90_mas > 0:
                return "rojo"
            if f.d31_60 > 0:
                return "ambar"
            return "verde"
        return [
            AgingProveedor(
                proveedor=f.proveedor, total_pendiente=f.total_pendiente,
                d0_30=f.d0_30, d31_60=f.d31_60, d61_90=f.d61_90, d90_mas=f.d90_mas,
                facturas=f.facturas, mas_vieja_dias=f.mas_vieja_dias, semaforo=_semaforo(f),
            )
            for f in filas
        ]

    async def proyeccion_caja(self) -> ProyeccionCaja:
        """Proyección del cierre del mes (fórmula del dashboard viejo, /proyeccion): promedio de
        los últimos 14 días CON movimiento × días restantes, sumado a lo REAL del mes en curso."""
        hoy = today_co()
        inicio14, fin14 = rango_dia_co(hoy - timedelta(days=13), hoy)
        ventas_14 = await self._repo.serie_ventas(inicio=inicio14, fin=fin14, vendedor_id=None)
        gastos_14 = await self._repo.gastos_por_dia(inicio=inicio14, fin=fin14)
        prom_ventas = _promedio_dias_con_movimiento(ventas_14)
        prom_gastos = _promedio_dias_con_movimiento(gastos_14)

        inicio_mes, fin_mes = rango_dia_co(hoy.replace(day=1), hoy)
        ventas_mes = await self._repo.total_ventas(inicio=inicio_mes, fin=fin_mes, vendedor_id=None)
        gastos_mes = sum(
            (t for _, t in await self._repo.gastos_por_dia(inicio=inicio_mes, fin=fin_mes)),
            Decimal("0"),
        )
        import calendar

        ultimo_dia = calendar.monthrange(hoy.year, hoy.month)[1]
        dias_restantes = ultimo_dia - hoy.day
        proy_ventas = cuantizar(ventas_mes + prom_ventas * dias_restantes)
        proy_gastos = cuantizar(gastos_mes + prom_gastos * dias_restantes)
        return ProyeccionCaja(
            dias_restantes=dias_restantes,
            promedio_venta_diaria=cuantizar(prom_ventas),
            promedio_gasto_diario=cuantizar(prom_gastos),
            ventas_mes_actual=ventas_mes, gastos_mes_actual=gastos_mes,
            proyeccion_ventas_mes=proy_ventas, proyeccion_gastos_mes=proy_gastos,
            proyeccion_neto_mes=proy_ventas - proy_gastos,
        )

    async def calendario(self, *, anio: int, mes: int, vendedor_id: int | None) -> list[DiaCalendarioLeer]:
        """Agregado diario del mes (heatmap): total, transacciones y gastos por día Colombia."""
        import calendar

        primero = date(anio, mes, 1)
        ultimo = date(anio, mes, calendar.monthrange(anio, mes)[1])
        inicio, fin = rango_dia_co(primero, ultimo)
        dias = await self._repo.calendario(inicio=inicio, fin=fin, vendedor_id=vendedor_id)
        return [
            DiaCalendarioLeer(
                fecha=d.fecha, total=d.total, num_ventas=d.num_ventas, gastos=d.gastos
            )
            for d in dias
        ]

    async def top_productos(
        self, *, desde: date | None, hasta: date | None, vendedor_id: int | None, limite: int
    ) -> list[TopProducto]:
        """Ranking de productos por ingreso del rango (default mes), acotado al vendedor efectivo."""
        d, h = _rango_o_mes(desde, hasta)
        inicio, fin = rango_dia_co(d, h)
        filas = await self._repo.top_productos(
            inicio=inicio, fin=fin, vendedor_id=vendedor_id, limite=limite
        )
        return [
            TopProducto(
                producto_id=f.producto_id, nombre=f.nombre, cantidad=f.cantidad, ingreso=f.ingreso
            )
            for f in filas
        ]
