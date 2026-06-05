"""Contratos Pydantic de reportes (salida del API)."""
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class ResumenDia(BaseModel):
    """KPIs del día para la pestaña Hoy del dashboard (api-contract.md / B4)."""

    fecha: date
    num_ventas: int
    total_vendido: Decimal
    ticket_promedio: Decimal
    por_metodo_pago: dict[str, Decimal]
