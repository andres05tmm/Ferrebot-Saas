"""Cobro de cita → venta (ADR 0022): vínculo contable en `citas`.

`venta_id` (FK a ventas, UNIQUE) es la mitad estructural de la idempotencia del cobro (la otra es
`ventas.idempotency_key = "cita-cobro:{cita_id}"`): una cita solo puede quedar vinculada a UNA
venta, y el UNIQUE lo garantiza a nivel de base aunque dos cobros corran en paralelo. `cobrada_en`
es la marca de tiempo del cobro (TIMESTAMPTZ, se opera en hora Colombia).

Aditiva y NULL-safe: las citas existentes de todos los tenants quedan con venta_id/cobrada_en NULL.

Revision ID: 0027_citas_cobro
Revises: 0026_pack_pagar
Create Date: 2026-07-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_citas_cobro"
down_revision: str | None = "0026_pack_pagar"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDICE = "uq_citas_venta_id"


def upgrade() -> None:
    op.add_column(
        "citas",
        sa.Column("venta_id", sa.BigInteger, sa.ForeignKey("ventas.id"), nullable=True),
    )
    op.add_column("citas", sa.Column("cobrada_en", sa.DateTime(timezone=True), nullable=True))
    op.execute(f"CREATE UNIQUE INDEX {_INDICE} ON citas (venta_id) WHERE venta_id IS NOT NULL")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDICE}")
    op.drop_column("citas", "cobrada_en")
    op.drop_column("citas", "venta_id")
