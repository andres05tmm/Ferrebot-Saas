"""Repositorio de reportes: único lugar con SQL (regla no negociable #2).

Agregación del día sobre `ventas`, EXCLUYENDO anuladas (solo `completada`), opcionalmente acotada a
un vendedor. Devuelve el agregado crudo (conteo, total y desglose por método de pago); el servicio
calcula derivados (ticket promedio) y arma el contrato de salida.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import case as sa_case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from modules.caja.models import Gasto
from modules.compras_fiscal.models import CompraFiscal
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
    costo_ventas: Decimal   # Σ SALIDA(costo×cant) − Σ DEVOLUCION(costo×cant), costo NULL = 0
    gastos: Decimal         # suma de gastos del rango


@dataclass(frozen=True, slots=True)
class AgregadoLibroIVA:
    """Insumos crudos del Libro IVA de un rango (el servicio deriva el saldo)."""

    base_ventas: Decimal       # Σ subtotal de ventas NO anuladas (base gravable de las ventas)
    iva_generado: Decimal      # Σ impuestos de ventas NO anuladas (IVA que se cobró)
    base_compras: Decimal      # Σ base de compras fiscales del rango
    iva_descontable: Decimal   # Σ iva de compras fiscales del rango (IVA que se puede descontar)


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

    async def serie_ventas(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None
    ) -> list[tuple[date, Decimal]]:
        """Serie diaria de ventas completadas del rango, agrupada por día en hora Colombia.

        Convierte `fecha` (TIMESTAMPTZ) a la fecha local de Bogotá (`timezone(...)` → `date`) para que
        cada venta caiga en su día Colombia. Excluye anuladas; acota a `vendedor_id` si se da. Devuelve
        solo los días CON ventas (el servicio rellena los vacíos en 0).
        """
        dia = func.date(func.timezone("America/Bogota", Venta.fecha)).label("dia")
        condiciones = [Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        stmt = (
            select(dia, func.coalesce(func.sum(Venta.total), 0).label("total"))
            .where(*condiciones)
            .group_by(dia)
            .order_by(dia)
        )
        filas = (await self._s.execute(stmt)).all()
        return [(f.dia, Decimal(f.total)) for f in filas]

    async def total_ventas(
        self, *, inicio: datetime, fin: datetime, vendedor_id: int | None
    ) -> Decimal:
        """Suma del total de ventas completadas del rango (acotada al vendedor si se da). 0 si no hay."""
        condiciones = [Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin]
        if vendedor_id is not None:
            condiciones.append(Venta.vendedor_id == vendedor_id)
        total = (
            await self._s.execute(
                select(func.coalesce(func.sum(Venta.total), 0)).where(*condiciones)
            )
        ).scalar_one()
        return Decimal(total)

    async def estado_resultados(
        self, *, inicio: datetime, fin: datetime
    ) -> AgregadoResultados:
        """Insumos del P&L del rango: ingresos (sin IVA), costo de ventas exacto y gastos.

        Ingresos = Σ subtotal de ventas completadas (el IVA es traslado, no ingreso). Costo de ventas =
        Σ(costo_unitario × cantidad) de movimientos SALIDA MENOS los DEVOLUCION (ADR 0026: una devolución
        re-ingresa mercancía al costo del snapshot original → revierte su COGS, sin distorsión por el
        promedio del día); un costo NULL (ventas previas al threading) cuenta como 0. Gastos = Σ monto de
        gastos del rango. Es del negocio completo (sin scoping).
        """
        ingresos = (
            await self._s.execute(
                select(func.coalesce(func.sum(Venta.subtotal), 0)).where(
                    Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin,
                )
            )
        ).scalar_one()
        # COGS anclado a la fecha de la venta origen (ADR 0025): `fecha_operacion` (snapshot de la
        # fecha de la venta al crear la SALIDA); cae a `creado_en` para movimientos previos a la 0029.
        fecha_cogs = func.coalesce(
            MovimientoInventario.fecha_operacion, MovimientoInventario.creado_en
        )
        # Signo por tipo: SALIDA suma al COGS, DEVOLUCION lo revierte (contra-COGS al costo snapshot).
        signo = sa_case(
            (MovimientoInventario.tipo == "DEVOLUCION", -1),
            else_=1,
        )
        costo_ventas = (
            await self._s.execute(
                select(
                    func.coalesce(
                        func.sum(
                            signo
                            * MovimientoInventario.cantidad
                            * func.coalesce(MovimientoInventario.costo_unitario, 0)
                        ),
                        0,
                    )
                ).where(
                    MovimientoInventario.tipo.in_(("SALIDA", "DEVOLUCION")),
                    fecha_cogs >= inicio,
                    fecha_cogs <= fin,
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

    async def libro_iva(self, *, inicio: datetime, fin: datetime) -> AgregadoLibroIVA:
        """Insumos del Libro IVA del rango: IVA generado (ventas) vs descontable (compras fiscales).

        IVA generado/base de ventas = Σ impuestos/subtotal de ventas completadas (anuladas excluidas);
        el IVA sale de la columna ya calculada al vender (no se recomputa por línea). IVA descontable/
        base de compras = Σ iva/base de `compras_fiscal` del rango (por `creado_en`). Es del negocio
        completo (soporte tributario, sin scoping).
        """
        base_ventas, iva_generado = (
            await self._s.execute(
                select(
                    func.coalesce(func.sum(Venta.subtotal), 0),
                    func.coalesce(func.sum(Venta.impuestos), 0),
                ).where(
                    Venta.estado == "completada", Venta.fecha >= inicio, Venta.fecha <= fin,
                )
            )
        ).one()
        base_compras, iva_descontable = (
            await self._s.execute(
                select(
                    func.coalesce(func.sum(CompraFiscal.base), 0),
                    func.coalesce(func.sum(CompraFiscal.iva), 0),
                ).where(
                    CompraFiscal.creado_en >= inicio, CompraFiscal.creado_en <= fin,
                )
            )
        ).one()
        return AgregadoLibroIVA(
            base_ventas=Decimal(base_ventas), iva_generado=Decimal(iva_generado),
            base_compras=Decimal(base_compras), iva_descontable=Decimal(iva_descontable),
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
