"""Servicio de reportes: deriva el resumen del día desde el agregado del repositorio.

Lógica pura y testeable: depende del puerto `ReportesRepo` (falseado en tests). Calcula el ticket
promedio (Decimal, 0 si no hubo ventas) y fija la fecha del día en hora Colombia.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from core.config.timezone import rango_dia_co, today_co
from core.money import cuantizar
from modules.reportes.repository import AgregadoDia
from modules.reportes.schemas import ResumenDia


class ReportesRepo(Protocol):
    """Puerto de datos de reportes (lo implementa SqlReportesRepository; los tests lo falsean)."""

    async def resumen(self, *, inicio, fin, vendedor_id: int | None) -> AgregadoDia: ...


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
