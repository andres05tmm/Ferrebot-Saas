"""costo_promedio en productos — COGS por promedio ponderado móvil (ADR 0025).

`productos.costo_promedio` es el costo unitario promedio ponderado del inventario disponible; lo
recalcula cada COMPRA bajo `SELECT ... FOR UPDATE` de la fila del producto y lo SNAPSHOTEA cada
SALIDA en `movimientos_inventario.costo_unitario` (antes se hilaba el último `precio_compra`). El
P&L no cambia de fórmula: sigue sumando los snapshots de los movimientos.

Aditiva y NULL-safe. Backfill: siembra `costo_promedio` desde el último `precio_compra` (los
movimientos históricos NO se tocan). Un producto sin `precio_compra` queda con `costo_promedio` NULL
y la venta cae al fallback (precio_compra) hasta su primera compra.

Revision ID: 0028_costo_promedio
Revises: 0027_citas_cobro
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_costo_promedio"
down_revision: str | None = "0027_citas_cobro"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("productos", sa.Column("costo_promedio", sa.Numeric(12, 2), nullable=True))
    # Siembra el promedio desde el costo de compra conocido; los movimientos históricos quedan intactos.
    op.execute("UPDATE productos SET costo_promedio = precio_compra WHERE precio_compra IS NOT NULL")


def downgrade() -> None:
    op.drop_column("productos", "costo_promedio")
