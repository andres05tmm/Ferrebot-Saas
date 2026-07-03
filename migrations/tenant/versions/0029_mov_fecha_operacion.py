"""fecha_operacion en movimientos_inventario — anclar el COGS a la fecha de la venta (ADR 0025).

El P&L (estado_resultados) mezclaba `Venta.fecha` (ingresos) con `MovimientoInventario.creado_en`
(costo de ventas). Al editar una venta de hoy, sus SALIDA se re-crean con un `creado_en` nuevo
(el instante de la edición) mientras la venta conserva su fecha original: ingreso y costo podían
caer en días distintos. `fecha_operacion` snapshotea la fecha del documento de negocio origen
(venta para SALIDA, compra para ENTRADA) y el P&L filtra por `coalesce(fecha_operacion, creado_en)`.

Aditiva y NULL-safe. Backfill: por defecto la fecha de inserción (`creado_en`); para las SALIDA
ligadas a una venta (tag `referencia = 'venta:{id}'`) se ancla a la fecha de esa venta. Los ajustes
quedan con NULL y el P&L usa su `creado_en` (no entran al costo de ventas de todos modos).

Revision ID: 0029_mov_fecha_operacion
Revises: 0028_costo_promedio
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_mov_fecha_operacion"
down_revision: str | None = "0028_costo_promedio"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "movimientos_inventario",
        sa.Column("fecha_operacion", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Default histórico: la fecha de inserción del movimiento.
    op.execute(
        "UPDATE movimientos_inventario SET fecha_operacion = creado_en "
        "WHERE fecha_operacion IS NULL"
    )
    # SALIDA de una venta: ancla a la fecha de la venta origen (tag `venta:{id}` en `referencia`).
    op.execute(
        "UPDATE movimientos_inventario m SET fecha_operacion = v.fecha "
        "FROM ventas v WHERE m.tipo = 'SALIDA' AND m.referencia = 'venta:' || v.id"
    )


def downgrade() -> None:
    op.drop_column("movimientos_inventario", "fecha_operacion")
