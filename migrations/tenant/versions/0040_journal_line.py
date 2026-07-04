"""Líneas de asiento — doble partida (ADR 0030, motor contable).

`direction` debit/credit con `amount` sin signo (> 0, CHECK). La validación débitos=créditos vive en
la app-layer (con la naturaleza de las cuentas a la vista) antes de postear; la base solo garantiza el
signo positivo. Borrado en cascada con el asiento.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0040_journal_line
Revises: 0039_journal_entry
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_journal_line"
down_revision: str | None = "0039_journal_entry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_line",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "entry_id", sa.BigInteger,
            sa.ForeignKey("journal_entry.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("cuenta_id", sa.BigInteger, sa.ForeignKey("puc_cuentas.id"), nullable=False),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("descripcion", sa.Text, nullable=True),
        sa.Column("orden", sa.SmallInteger, nullable=False, server_default="0"),
        sa.CheckConstraint("direction IN ('debit','credit')", name="ck_journal_line_direction"),
        sa.CheckConstraint("amount > 0", name="ck_journal_line_amount_positivo"),
    )
    op.create_index("ix_journal_line_entry", "journal_line", ["entry_id"])
    op.create_index("ix_journal_line_cuenta", "journal_line", ["cuenta_id"])


def downgrade() -> None:
    op.drop_index("ix_journal_line_cuenta", table_name="journal_line")
    op.drop_index("ix_journal_line_entry", table_name="journal_line")
    op.drop_table("journal_line")
