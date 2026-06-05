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

from modules.caja.models import Gasto
from modules.inventario.models import MovimientoInventario, Producto
from modules.ventas.models import Venta, VentaDetalle


@dataclass(frozen=True, slots=True)
class AgregadoDia:
    """Agregado crudo del día (ya excluidas las anuladas)."""

    num_ventas: int
    total_vendido: Decimal
    por_metodo_pago: dict[str, Decimal]


@dataclass(frozen=True, slots=True)
class AgregadoResultados:
    """Insumos crudos del estado de resultados de un rango (el servicio deriva utilidades)."""

    ingresos: Decimal       # suma de subtotal (sin IVA) de ventas NO anuladas
    costo_ventas: Decimal   # suma(costo_unitario × cantidad) de movimientos SALIDA (NULL = 0)
    gastos: Decimal         # suma de gastos del rango


@dataclass(frozen=True, slots=True)
class TopProductoFila:
    """Una fila del ranking de productos (cantidad e ingreso agregados)."""

    producto_id: int
    nombre: str
    cantidad: Decimal
    ingreso: Decimal


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

    async def estado_resultados(
        self, *, inicio: datetime, fin: datetime
    ) -> AgregadoResultados:
        """Insumos del P&L del rango: ingresos (sin IVA), costo de ventas exacto y gastos.

        Ingresos = Σ subtotal de ventas completadas (el IVA es traslado, no ingreso). Costo de ventas =
        Σ(costo_unitario × cantidad) de movimientos SALIDA; un costo NULL (ventas previas al threading)
        cuenta como 0. Gastos = Σ monto de gastos del rango. Es del negocio completo (sin scoping).
        """
        ingresos = (
            await self._s.execute(
                select(func.coalesce(func.sum(Venta.subtotal), 0)).where(
                    Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin,
                )
            )
        ).scalar_one()
        costo_ventas = (
            await self._s.execute(
                select(
                    func.coalesce(
                        func.sum(
                            MovimientoInventario.cantidad
                            * func.coalesce(MovimientoInventario.costo_unitario, 0)
                        ),
                        0,
                    )
                ).where(
                    MovimientoInventario.tipo == "SALIDA",
                    MovimientoInventario.creado_en >= inicio,
                    MovimientoInventario.creado_en <= fin,
                )
            )
        ).scalar_one()
        gastos = (
            await self._s.execute(
                select(func.coalesce(func.sum(Gasto.monto), 0)).where(
                    Gasto.creado_en >= inicio, Gasto.creado_en <= fin,
                )
            )
        ).scalar_one()
        return AgregadoResultados(
            ingresos=Decimal(ingresos), costo_ventas=Decimal(costo_ventas), gastos=Decimal(gastos)
        )

    async def top_productos(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None, limite: int
    ) -> list[TopProductoFila]:
        """Ranking de productos por ingreso (cantidad × precio) en el rango, de ventas completadas.

        Agrupa `ventas_detalle` (join `ventas` y `productos`), excluye las varia (sin producto_id) y
        las ventas anuladas. `vendedor_id` lo acota a un vendedor; `None` = todo el negocio. Orden por
        ingreso descendente.
        """
        ingreso_expr = func.coalesce(
            func.sum(VentaDetalle.cantidad * VentaDetalle.precio_unitario), 0
        )
        condiciones = [
            Venta.estado == "completada",
            Venta.fecha >= inicio,
            Venta.fecha <= fin,
            VentaDetalle.producto_id.is_not(None),
        ]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        stmt = (
            select(
                VentaDetalle.producto_id,
                Producto.nombre,
                func.coalesce(func.sum(VentaDetalle.cantidad), 0).label("cantidad"),
                ingreso_expr.label("ingreso"),
            )
            .join(Venta, Venta.id == VentaDetalle.venta_id)
            .join(Producto, Producto.id == VentaDetalle.producto_id)
            .where(*condiciones)
            .group_by(VentaDetalle.producto_id, Producto.nombre)
            .order_by(ingreso_expr.desc())
            .limit(limite)
        )
        filas = (await self._s.execute(stmt)).all()
        return [
            TopProductoFila(
                producto_id=fila.producto_id, nombre=fila.nombre,
                cantidad=Decimal(fila.cantidad), ingreso=Decimal(fila.ingreso),
            )
            for fila in filas
        ]
