"""Modificadores de menú (F2 Pack Restaurante, ADR 0032 D3).

Catálogo relacional (grupos por producto con min/max/obligatorio + opciones con delta de precio) y
SNAPSHOT JSONB en `pedido_items.modificadores` (lista [{grupo, opcion, delta_precio}] al momento del
pedido: el catálogo puede cambiar después, el pedido no — mismo principio que nombre/precio).

Aditivo y NULL-safe (esquema compartido por todos los verticales; tabla vacía no cuesta).

Revision ID: 0060_modificadores
Revises: 0059_pedido_venta
Create Date: 2026-07-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0060_modificadores"
down_revision: str | None = "0059_pedido_venta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "modificador_grupos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "producto_id", sa.BigInteger,
            sa.ForeignKey("productos.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("min_sel", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_sel", sa.Integer, nullable=True),   # NULL = sin tope
        sa.Column("obligatorio", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("orden", sa.Integer, nullable=False, server_default="0"),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_modificador_grupos_producto", "modificador_grupos", ["producto_id"])
    op.create_table(
        "modificador_opciones",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "grupo_id", sa.BigInteger,
            sa.ForeignKey("modificador_grupos.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("delta_precio", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_modificador_opciones_grupo", "modificador_opciones", ["grupo_id"])
    op.add_column("pedido_items", sa.Column("modificadores", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("pedido_items", "modificadores")
    op.drop_index("ix_modificador_opciones_grupo", table_name="modificador_opciones")
    op.drop_table("modificador_opciones")
    op.drop_index("ix_modificador_grupos_producto", table_name="modificador_grupos")
    op.drop_table("modificador_grupos")
