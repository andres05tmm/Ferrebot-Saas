"""Servicio de reportes: deriva el resumen del día desde el agregado del repositorio.

Lógica pura y testeable: depende del puerto `ReportesRepo` (falseado en tests). Calcula el ticket
promedio (Decimal, 0 si no hubo ventas) y fija la fecha del día en hora Colombia.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Protocol

from core.config.timezone import rango_dia_co, today_co
from core.money import cuantizar
from modules.reportes.repository import AgregadoDia, AgregadoResultados, TopProductoFila
from modules.reportes.schemas import EstadoResultados, ResumenDia, TopProducto


class ReportesRepo(Protocol):
    """Puerto de datos de reportes (lo implementa SqlReportesRepository; los tests lo falsean)."""

    async def resumen(self, *, inicio, fin, vendedor_id: int | None) -> AgregadoDia: ...
    async def estado_resultados(self, *, inicio, fin) -> AgregadoResultados: ...
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
