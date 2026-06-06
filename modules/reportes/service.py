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
    AgregadoDia,
    AgregadoLibroIVA,
    AgregadoResultados,
    TopProductoFila,
)
from modules.reportes.schemas import (
    EstadoResultados,
    LibroIVA,
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


def _rango_o_mes(desde: date | None, hasta: date | None) -> tuple[date, date]:
    """Resuelve el rango: ausente → mes en curso (día 1 → hoy Colombia). Nunca date.today() crudo."""
    hoy = today_co()
    return (desde or hoy.replace(day=1)), (hasta or hoy)


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
