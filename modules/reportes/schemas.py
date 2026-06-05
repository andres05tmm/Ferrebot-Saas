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


class EstadoResultados(BaseModel):
    """Estado de resultados (P&L) de un rango para la pestaña Resultados (Fase 12, Slice 2)."""

    desde: date
    hasta: date
    ingresos: Decimal           # ventas sin IVA (el IVA es traslado)
    costo_ventas: Decimal       # costo de la mercancía vendida (exacto desde el threading por venta)
    utilidad_bruta: Decimal     # ingresos − costo_ventas
    gastos: Decimal
    utilidad_neta: Decimal      # utilidad_bruta − gastos


class TopProducto(BaseModel):
    """Una fila del ranking de productos por cantidad e ingreso en un rango."""

    producto_id: int
    nombre: str
    cantidad: Decimal
    ingreso: Decimal
