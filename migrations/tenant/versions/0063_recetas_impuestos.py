"""Recetas/BOM + tipo de impuesto + recargo por plato (F6 Pack Restaurante, ADR 0032 D2/D8/D9).

- `recetas`: BOM del plato (insumo = producto del catálogo CON inventario; el plato no lleva stock).
- `productos.tipo_impuesto` / `ventas_detalle.tipo_impuesto` ('iva' | 'inc'): el impoconsumo 8% SE
  MODELA — la tarifa vive en la columna `iva` de siempre; IVA e INC coexisten porque el esquema es
  compartido con ferreterías. Default 'iva' = cero cambio para los tenants existentes.
- `zonas_domicilio.recargo_por_item`: recargo POR PLATO de la zona (caso real Bocagrande
  +$1.000/plato); default 0 = tarifa plana de siempre.

Aditivo y NULL-safe con defaults.

Revision ID: 0063_recetas_impuestos
Revises: 0062_kds
Create Date: 2026-07-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_recetas_impuestos"
down_revision: str | None = "0062_kds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recetas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "producto_id", sa.BigInteger,
            sa.ForeignKey("productos.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "insumo_id", sa.BigInteger,
            sa.ForeignKey("productos.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
        sa.UniqueConstraint("producto_id", "insumo_id", name="uq_recetas_producto_insumo"),
    )
    op.create_index("ix_recetas_producto", "recetas", ["producto_id"])
    op.add_column(
        "productos",
        sa.Column("tipo_impuesto", sa.Text, nullable=False, server_default="iva"),
    )
    op.add_column(
        "ventas_detalle",
        sa.Column("tipo_impuesto", sa.Text, nullable=False, server_default="iva"),
    )
    op.add_column(
        "zonas_domicilio",
        sa.Column("recargo_por_item", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("zonas_domicilio", "recargo_por_item")
    op.drop_column("ventas_detalle", "tipo_impuesto")
    op.drop_column("productos", "tipo_impuesto")
    op.drop_index("ix_recetas_producto", table_name="recetas")
    op.drop_table("recetas")
