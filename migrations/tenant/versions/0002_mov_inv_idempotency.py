"""mov_inventario idempotency_key — idempotencia estructural del ajuste de stock.

Agrega `idempotency_key TEXT NULL` a movimientos_inventario + índice UNIQUE parcial
(solo sobre filas con key). Los movimientos existentes (venta, etc.) quedan con key NULL
y no chocan con el índice parcial.

Revision ID: 0002_mov_inv_idem
Revises: 0001_tenant
Create Date: 2026-06-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_mov_inv_idem"
down_revision: str | None = "0001_tenant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDICE = "uq_mov_inv_idempotency_key"


def upgrade() -> None:
    op.add_column("movimientos_inventario", sa.Column("idempotency_key", sa.Text, nullable=True))
    op.execute(
        f"CREATE UNIQUE INDEX {_INDICE} ON movimientos_inventario (idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDICE}")
    op.drop_column("movimientos_inventario", "idempotency_key")
