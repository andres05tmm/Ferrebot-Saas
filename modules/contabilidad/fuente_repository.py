"""Lecturas de las tablas OPERATIVAS que el proyector traduce a asientos (ADR 0030).

Es la capa de repositorio del proyector: encapsula el SQL contra `ventas`, `gastos`,
`fiados_movimientos`, `compras`/`compras_fiscal`, `facturas_abonos`, `devoluciones` y
`retenciones_documento`, devolviendo dataclasses de evento (nunca filas crudas al servicio). Solo
lee; el proyector no muta las tablas origen. Sin commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class VentaEvento:
    id: int
    fecha: datetime
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    metodo_pago: str
    estado: str
    costo: Decimal   # Σ costo_unitario·cantidad de las SALIDA de esta venta (COGS)


@dataclass(frozen=True, slots=True)
class GastoEvento:
    id: int
    fecha: datetime
    monto: Decimal
    categoria: str
    salda_cxp: bool   # tiene abono_proveedor_id → el pago lo captura el abono (no duplicar)


@dataclass(frozen=True, slots=True)
class AbonoFiadoEvento:
    id: int
    fecha: datetime
    monto: Decimal


@dataclass(frozen=True, slots=True)
class CompraEvento:
    id: int
    fecha: datetime
    inventario: Decimal   # base (valor de mercancía sin IVA)
    iva: Decimal
    total: Decimal


@dataclass(frozen=True, slots=True)
class AbonoProveedorEvento:
    id: int
    fecha: datetime
    monto: Decimal


@dataclass(frozen=True, slots=True)
class DevolucionEvento:
    id: int
    fecha: datetime
    total: Decimal
    metodo_reintegro: str
    costo: Decimal
    venta_impuestos: Decimal
    venta_total: Decimal


@dataclass(frozen=True, slots=True)
class RetencionEvento:
    id: int
    fecha: datetime
    doc_tipo: str   # 'venta' | 'compra'
    doc_id: int
    tipo: str       # 'retefuente' | 'ica' | 'reteiva' | 'inc'
    valor: Decimal
    metodo_pago_doc: str | None   # método de la venta origen (para elegir Caja/Clientes)


def _dec(v) -> Decimal:
    return Decimal(v) if v is not None else Decimal("0")


class FuenteContableRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def venta(self, venta_id: int) -> VentaEvento | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT v.id, v.fecha, v.subtotal, v.impuestos, v.total, v.metodo_pago, v.estado, "
                    "COALESCE((SELECT SUM(m.costo_unitario*m.cantidad) FROM movimientos_inventario m "
                    "  WHERE m.referencia = 'venta:'||v.id AND m.tipo='SALIDA'),0) AS costo "
                    "FROM ventas v WHERE v.id=:id"
                ),
                {"id": venta_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return VentaEvento(
            id=row.id, fecha=row.fecha, subtotal=_dec(row.subtotal), impuestos=_dec(row.impuestos),
            total=_dec(row.total), metodo_pago=row.metodo_pago, estado=row.estado, costo=_dec(row.costo),
        )

    async def gasto(self, gasto_id: int) -> GastoEvento | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT id, creado_en, monto, categoria, (abono_proveedor_id IS NOT NULL) AS salda "
                    "FROM gastos WHERE id=:id"
                ),
                {"id": gasto_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return GastoEvento(
            id=row.id, fecha=row.creado_en, monto=_dec(row.monto),
            categoria=row.categoria, salda_cxp=bool(row.salda),
        )

    async def abono_fiado(self, mov_id: int) -> AbonoFiadoEvento | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT id, creado_en, monto FROM fiados_movimientos WHERE id=:id AND tipo='abono'"
                ),
                {"id": mov_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return AbonoFiadoEvento(id=row.id, fecha=row.creado_en, monto=_dec(row.monto))

    async def compra(self, compra_id: int) -> CompraEvento | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT c.id, c.fecha, c.total, "
                    "  COALESCE((SELECT SUM(d.costo*d.cantidad) FROM compras_detalle d WHERE d.compra_id=c.id),0) AS det, "
                    "  f.base AS fbase, f.iva AS fiva, f.total AS ftotal "
                    "FROM compras c LEFT JOIN compras_fiscal f ON f.compra_id=c.id WHERE c.id=:id"
                ),
                {"id": compra_id},
            )
        ).one_or_none()
        if row is None:
            return None
        if row.ftotal is not None:
            total = _dec(row.ftotal)
            iva = _dec(row.fiva)
            inv = _dec(row.fbase) if row.fbase is not None else total - iva
        else:
            total = _dec(row.total) if row.total is not None else _dec(row.det)
            iva = Decimal("0")
            inv = total
        return CompraEvento(id=row.id, fecha=row.fecha, inventario=inv, iva=iva, total=total)

    async def abono_proveedor(self, abono_id: int) -> AbonoProveedorEvento | None:
        row = (
            await self._s.execute(
                text("SELECT id, creado_en, monto FROM facturas_abonos WHERE id=:id"),
                {"id": abono_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return AbonoProveedorEvento(id=row.id, fecha=row.creado_en, monto=_dec(row.monto))

    async def devolucion(self, dev_id: int) -> DevolucionEvento | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT d.id, d.creado_en, d.total, d.metodo_reintegro, "
                    "  COALESCE((SELECT SUM(COALESCE(dd.costo_unitario,0)*dd.cantidad) FROM devoluciones_detalle dd "
                    "    WHERE dd.devolucion_id=d.id),0) AS costo, "
                    "  v.impuestos AS vimp, v.total AS vtot "
                    "FROM devoluciones d JOIN ventas v ON v.id=d.venta_id WHERE d.id=:id"
                ),
                {"id": dev_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return DevolucionEvento(
            id=row.id, fecha=row.creado_en, total=_dec(row.total),
            metodo_reintegro=row.metodo_reintegro, costo=_dec(row.costo),
            venta_impuestos=_dec(row.vimp), venta_total=_dec(row.vtot),
        )

    async def retencion(self, retencion_id: int) -> RetencionEvento | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT r.id, r.creado_en, r.doc_tipo, r.doc_id, r.tipo, r.valor, "
                    "  (SELECT v.metodo_pago FROM ventas v WHERE v.id=r.doc_id AND r.doc_tipo='venta') AS mp "
                    "FROM retenciones_documento r WHERE r.id=:id"
                ),
                {"id": retencion_id},
            )
        ).one_or_none()
        if row is None:
            return None
        return RetencionEvento(
            id=row.id, fecha=row.creado_en, doc_tipo=row.doc_tipo, doc_id=row.doc_id,
            tipo=row.tipo, valor=_dec(row.valor), metodo_pago_doc=row.mp,
        )

    # --- ids para el backfill (solo hacia adelante desde una fecha) ------------
    async def ids_desde(self, tabla: str, columna_fecha: str, desde: datetime, filtro: str = "") -> list[int]:
        q = f"SELECT id FROM {tabla} WHERE {columna_fecha} >= :desde {filtro} ORDER BY id"
        rows = (await self._s.execute(text(q), {"desde": desde})).all()
        return [r.id for r in rows]
