"""Servicio de compras: orquesta get-or-create de proveedor, cálculo de total y registro.

Lógica de dominio (sin SQL): resuelve el proveedor, calcula el total en el SERVIDOR (Σ cantidad×costo)
y delega el registro transaccional (stock + costo + eventos) en el repositorio. La fecha y el rango
default usan hora Colombia (regla #4).
"""
from datetime import date, datetime, time
from decimal import Decimal

from core.config.timezone import COLOMBIA_TZ, now_co, rango_dia_co, today_co
from core.money import cuantizar
from modules.compras.repository import ItemCompra, SqlComprasRepository
from modules.compras.schemas import CompraCrear, CompraLeer


def _fecha_compra(fecha: date | None) -> datetime:
    """Fecha de la compra como datetime aware Colombia: la dada (mediodía) o ahora."""
    if fecha is None:
        return now_co()
    return datetime.combine(fecha, time(12, 0), tzinfo=COLOMBIA_TZ)


def _rango_o_mes(desde: date | None, hasta: date | None) -> tuple[datetime, datetime]:
    """Ventana [inicio, fin] aware: rango dado o, si falta, el mes en curso (día 1 → hoy Colombia)."""
    hoy = today_co()
    return rango_dia_co(desde or hoy.replace(day=1), hasta or hoy)


class ComprasService:
    def __init__(self, repo: SqlComprasRepository) -> None:
        self._repo = repo

    async def registrar(self, datos: CompraCrear, *, usuario_id: int | None) -> CompraLeer:
        """Registra la compra: resuelve proveedor, calcula total y persiste (stock + costo + eventos)."""
        proveedor_id = await self._repo.get_or_create_proveedor(
            proveedor_id=datos.proveedor.id, nombre=datos.proveedor.nombre, nit=datos.proveedor.nit,
        )
        items = [
            ItemCompra(producto_id=it.producto_id, cantidad=it.cantidad, costo=it.costo)
            for it in datos.items
        ]
        total = cuantizar(sum((it.cantidad * it.costo for it in items), Decimal("0")))
        return await self._repo.crear_compra(
            proveedor_id=proveedor_id, fecha=_fecha_compra(datos.fecha),
            items=items, total=total, usuario_id=usuario_id,
        )

    async def listar(self, *, desde: date | None, hasta: date | None) -> list[CompraLeer]:
        """Compras del rango (default mes en curso, hora Colombia)."""
        inicio, fin = _rango_o_mes(desde, hasta)
        return await self._repo.listar(inicio=inicio, fin=fin)
