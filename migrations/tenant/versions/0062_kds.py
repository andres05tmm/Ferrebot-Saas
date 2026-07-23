"""Comandas KDS (F4 Pack Restaurante, ADR 0032 D5): zonas de cocina + cola por comanda.

- `comanda_zonas`: parrilla, bar, … (`productos.zona_comanda_id` rutea; NULL = cocina general).
- `comandas`: una por (pedido, zona) al confirmar el pedido / por ronda de mesa; estado
  pendiente → en_preparacion → listo con timestamps de auditoría.
- `comanda_items`: los ítems del pedido que caen en esa comanda (referencia, no copia de precios —
  el KDS es una VISTA sobre los datos del pedido).

Aditivo y NULL-safe (tabla vacía no cuesta en los demás verticales).

Revision ID: 0062_kds
Revises: 0061_mesas
Create Date: 2026-07-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_kds"
down_revision: str | None = "0061_mesas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comanda_zonas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "productos",
        sa.Column(
            "zona_comanda_id", sa.BigInteger,
            sa.ForeignKey("comanda_zonas.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_table(
        "comandas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "pedido_id", sa.BigInteger,
            sa.ForeignKey("pedidos.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "zona_id", sa.BigInteger,
            sa.ForeignKey("comanda_zonas.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("estado", sa.Text, nullable=False, server_default="pendiente"),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lista_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_comandas_pedido", "comandas", ["pedido_id"])
    op.create_index("ix_comandas_estado", "comandas", ["estado"])
    op.create_table(
        "comanda_items",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "comanda_id", sa.BigInteger,
            sa.ForeignKey("comandas.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "pedido_item_id", sa.BigInteger,
            sa.ForeignKey("pedido_items.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
    )
    op.create_index("ix_comanda_items_comanda", "comanda_items", ["comanda_id"])


def downgrade() -> None:
    op.drop_index("ix_comanda_items_comanda", table_name="comanda_items")
    op.drop_table("comanda_items")
    op.drop_index("ix_comandas_estado", table_name="comandas")
    op.drop_index("ix_comandas_pedido", table_name="comandas")
    op.drop_table("comandas")
    op.drop_column("productos", "zona_comanda_id")
    op.drop_table("comanda_zonas")
