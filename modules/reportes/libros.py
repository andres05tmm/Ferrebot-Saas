"""Libros auxiliar y mayor (ADR 0027): reportes contables derivados de los datos que ya existen.

Todavía SIN PUC formal (eso es F8): las "cuentas" son conceptos coarse del negocio (ingresos, IVA
generado/descontable, costo de ventas, gastos, compras y las retenciones/INC por tipo). El **Mayor**
totaliza cada concepto en el período; el **Auxiliar** lista el detalle documento a documento detrás de
cada concepto. Solo lectura, hora Colombia, del negocio completo (soporte contable, sin scoping).

SQL solo aquí (regla #2). El costo de ventas se ancla a `fecha_operacion` (ADR 0025), igual que el P&L.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import rango_dia_co, today_co
from modules.reportes.schemas import CuentaMayor, MovimientoAuxiliar

# concepto → naturaleza (agrupación provisional mientras no hay PUC, ADR 0027 / F8).
NATURALEZA: dict[str, str] = {
    "ingresos_ventas": "ingreso",
    "iva_generado": "impuesto",
    "costo_ventas": "egreso",
    "gastos": "egreso",
    "compras": "egreso",
    "iva_descontable": "impuesto",
    "retefuente": "retencion",
    "ica": "retencion",
    "reteiva": "retencion",
    "inc": "impuesto",
}


def _rango_o_mes(desde: date | None, hasta: date | None) -> tuple[datetime, datetime]:
    hoy = today_co()
    return rango_dia_co(desde or hoy.replace(day=1), hasta or hoy)


class SqlLibrosRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _escalar(self, sql: str, params: dict) -> Decimal:
        return Decimal((await self._s.execute(text(sql), params)).scalar_one())

    async def mayor(self, *, inicio: datetime, fin: datetime) -> list[CuentaMayor]:
        """Totales por concepto en el período. Omite los conceptos en cero."""
        p = {"inicio": inicio, "fin": fin}
        totales: dict[str, Decimal] = {}
        totales["ingresos_ventas"] = await self._escalar(
            "SELECT coalesce(sum(subtotal),0) FROM ventas WHERE estado='completada' "
            "AND fecha >= :inicio AND fecha <= :fin", p,
        )
        totales["iva_generado"] = await self._escalar(
            "SELECT coalesce(sum(impuestos),0) FROM ventas WHERE estado='completada' "
            "AND fecha >= :inicio AND fecha <= :fin", p,
        )
        # Costo de ventas anclado a fecha_operacion (cae a creado_en para movimientos previos a 0029).
        totales["costo_ventas"] = await self._escalar(
            "SELECT coalesce(sum(cantidad * coalesce(costo_unitario,0)),0) FROM movimientos_inventario "
            "WHERE tipo='SALIDA' AND coalesce(fecha_operacion, creado_en) >= :inicio "
            "AND coalesce(fecha_operacion, creado_en) <= :fin", p,
        )
        totales["gastos"] = await self._escalar(
            "SELECT coalesce(sum(monto),0) FROM gastos WHERE creado_en >= :inicio AND creado_en <= :fin", p,
        )
        totales["compras"] = await self._escalar(
            "SELECT coalesce(sum(base),0) FROM compras_fiscal WHERE creado_en >= :inicio AND creado_en <= :fin", p,
        )
        totales["iva_descontable"] = await self._escalar(
            "SELECT coalesce(sum(iva),0) FROM compras_fiscal WHERE creado_en >= :inicio AND creado_en <= :fin", p,
        )
        # Retenciones/INC por tipo (retenciones_documento, por creado_en).
        filas = (
            await self._s.execute(
                text(
                    "SELECT tipo, coalesce(sum(valor),0) FROM retenciones_documento "
                    "WHERE creado_en >= :inicio AND creado_en <= :fin GROUP BY tipo"
                ),
                p,
            )
        ).all()
        for tipo, total in filas:
            totales[tipo] = Decimal(total)

        return [
            CuentaMayor(concepto=c, naturaleza=NATURALEZA.get(c, "otro"), total=v)
            for c, v in totales.items()
            if v != 0
        ]

    async def auxiliar(
        self, *, inicio: datetime, fin: datetime, concepto: str | None
    ) -> list[MovimientoAuxiliar]:
        """Detalle documento a documento (ventas, compras, gastos, retenciones). Filtrable por concepto."""
        dia_local = "date(timezone('America/Bogota', {col}))"
        movimientos: list[MovimientoAuxiliar] = []

        async def _agregar(sql: str, concepto_fijo: str) -> None:
            if concepto is not None and concepto != concepto_fijo:
                return
            filas = (await self._s.execute(text(sql), {"inicio": inicio, "fin": fin})).all()
            for f in filas:
                movimientos.append(
                    MovimientoAuxiliar(
                        fecha=f[0], concepto=concepto_fijo,
                        naturaleza=NATURALEZA.get(concepto_fijo, "otro"),
                        referencia=str(f[1]), valor=Decimal(f[2]),
                    )
                )

        await _agregar(
            f"SELECT {dia_local.format(col='fecha')} AS d, 'venta:' || consecutivo, subtotal "
            "FROM ventas WHERE estado='completada' AND fecha >= :inicio AND fecha <= :fin ORDER BY d",
            "ingresos_ventas",
        )
        await _agregar(
            f"SELECT {dia_local.format(col='creado_en')} AS d, 'compra_fiscal:' || id, coalesce(base,0) "
            "FROM compras_fiscal WHERE creado_en >= :inicio AND creado_en <= :fin ORDER BY d",
            "compras",
        )
        await _agregar(
            f"SELECT {dia_local.format(col='creado_en')} AS d, 'gasto:' || id, monto "
            "FROM gastos WHERE creado_en >= :inicio AND creado_en <= :fin ORDER BY d",
            "gastos",
        )
        # Retenciones: un movimiento por renglón; filtra por concepto = el tipo de retención.
        ret_where = "creado_en >= :inicio AND creado_en <= :fin"
        ret_sql = (
            f"SELECT {dia_local.format(col='creado_en')} AS d, doc_tipo || ':' || doc_id, valor, tipo "
            f"FROM retenciones_documento WHERE {ret_where} ORDER BY d"
        )
        for f in (await self._s.execute(text(ret_sql), {"inicio": inicio, "fin": fin})).all():
            tipo = f[3]
            if concepto is not None and concepto != tipo:
                continue
            movimientos.append(
                MovimientoAuxiliar(
                    fecha=f[0], concepto=tipo, naturaleza=NATURALEZA.get(tipo, "retencion"),
                    referencia=str(f[1]), valor=Decimal(f[2]),
                )
            )
        movimientos.sort(key=lambda m: (m.fecha, m.concepto, m.referencia))
        return movimientos


class LibrosService:
    """Libros auxiliar y mayor sobre el rango dado (default mes en curso, hora Colombia)."""

    def __init__(self, repo: SqlLibrosRepository) -> None:
        self._repo = repo

    async def mayor(self, *, desde: date | None, hasta: date | None) -> list[CuentaMayor]:
        inicio, fin = _rango_o_mes(desde, hasta)
        return await self._repo.mayor(inicio=inicio, fin=fin)

    async def auxiliar(
        self, *, desde: date | None, hasta: date | None, concepto: str | None
    ) -> list[MovimientoAuxiliar]:
        inicio, fin = _rango_o_mes(desde, hasta)
        return await self._repo.auxiliar(inicio=inicio, fin=fin, concepto=concepto)
