"""idempotency_key en las operaciones que mueven dinero (caja/gastos/fiados).

Agrega `idempotency_key TEXT NULL` + índice UNIQUE parcial (WHERE NOT NULL) a las tablas ancla
de cada operación idempotente: caja_movimientos, gastos, fiados, fiados_movimientos. Mismo patrón
estructural que la 0002 (ajuste de inventario). No rompe filas existentes (key NULL).

Revision ID: 0003_dinero_idem
Revises: 0002_mov_inv_idem
Create Date: 2026-06-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_dinero_idem"
down_revision: str | None = "0002_mov_inv_idem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLAS = ("caja_movimientos", "gastos", "fiados", "fiados_movimientos")


def upgrade() -> None:
    for tabla in _TABLAS:
        op.add_column(tabla, sa.Column("idempotency_key", sa.Text, nullable=True))
        op.execute(
            f"CREATE UNIQUE INDEX uq_{tabla}_idempotency_key ON {tabla} (idempotency_key) "
            "WHERE idempotency_key IS NOT NULL"
        )


def downgrade() -> None:
    for tabla in _TABLAS:
        op.execute(f"DROP INDEX IF EXISTS uq_{tabla}_idempotency_key")
        op.drop_column(tabla, "idempotency_key")
