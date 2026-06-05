"""Repositorio de reportes: único lugar con SQL (regla no negociable #2).

Agregación del día sobre `ventas`, EXCLUYENDO anuladas (solo `completada`), opcionalmente acotada a
un vendedor. Devuelve el agregado crudo (conteo, total y desglose por método de pago); el servicio
calcula derivados (ticket promedio) y arma el contrato de salida.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.ventas.models import Venta


@dataclass(frozen=True, slots=True)
class AgregadoDia:
    """Agregado crudo del día (ya excluidas las anuladas)."""

    num_ventas: int
    total_vendido: Decimal
    por_metodo_pago: dict[str, Decimal]


class SqlReportesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def resumen(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None
    ) -> AgregadoDia:
        """Agrupa las ventas completadas del rango por método de pago; suma conteo y total."""
        condiciones = [
            Venta.estado == "completada",
            Venta.fecha >= inicio,
            Venta.fecha <= fin,
        ]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        stmt = (
            select(
                Venta.metodo_pago,
                func.count().label("num"),
                func.coalesce(func.sum(Venta.total), 0).label("total"),
            )
            .where(*condiciones)
            .group_by(Venta.metodo_pago)
        )
        filas = (await self._s.execute(stmt)).all()
        por_metodo = {fila.metodo_pago: Decimal(fila.total) for fila in filas}
        num_ventas = sum(fila.num for fila in filas)
        total_vendido = sum((Decimal(fila.total) for fila in filas), Decimal("0"))
        return AgregadoDia(
            num_ventas=num_ventas, total_vendido=total_vendido, por_metodo_pago=por_metodo
        )
