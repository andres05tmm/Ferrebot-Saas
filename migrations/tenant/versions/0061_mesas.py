"""Mesas y salón (F3 Pack Restaurante, ADR 0032 D4): orden abierta por mesa sobre `pedidos`.

- `mesas`: el salón físico (nombre/zona/activo).
- Estado nuevo `abierto` en el enum `pedido_estado` (aditivo; los flujos de domicilio nunca lo usan
  — las transiciones lo acotan por `origen`). Se agrega en un bloque AUTOCOMMIT porque Postgres no
  permite usar un valor de enum añadido en la misma transacción (el índice parcial de abajo lo usa).
- `pedidos.mesa_id` (FK) + índice parcial UNIQUE: UNA sola orden abierta por mesa.

Aditivo y NULL-safe. El downgrade quita tabla/columna/índice; el valor de enum queda (Postgres no
soporta quitar valores; inofensivo sin filas que lo usen).

Revision ID: 0061_mesas
Revises: 0060_modificadores
Create Date: 2026-07-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_mesas"
down_revision: str | None = "0060_modificadores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE pedido_estado ADD VALUE IF NOT EXISTS 'abierto'")
    op.create_table(
        "mesas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("zona", sa.Text, nullable=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "pedidos",
        sa.Column("mesa_id", sa.BigInteger, sa.ForeignKey("mesas.id", ondelete="SET NULL")),
    )
    op.create_index(
        "uq_pedidos_mesa_abierta", "pedidos", ["mesa_id"],
        unique=True, postgresql_where=sa.text("estado = 'abierto'"),
    )


def downgrade() -> None:
    op.drop_index("uq_pedidos_mesa_abierta", table_name="pedidos")
    op.drop_column("pedidos", "mesa_id")
    op.drop_table("mesas")
