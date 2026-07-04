"""Asientos contables — cabecera inmutable append-only (ADR 0030, motor contable).

Un asiento `posted` no se edita: la corrección es un asiento espejo (`reverso_de`). `idempotency_key`
UNIQUE ancla la idempotencia del proyector (un evento → un asiento). `origen_tipo`/`origen_id` ligan
el asiento al evento operativo que lo generó (venta, gasto, fiado, compra, devolución, retención,
apertura). `periodo_id` referencia el período cuyo candado se valida antes de postear.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0039_journal_entry
Revises: 0038_periodo_contable
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_journal_entry"
down_revision: str | None = "0038_periodo_contable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_entry",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("fecha", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "periodo_id", sa.BigInteger, sa.ForeignKey("periodo_contable.id"), nullable=True
        ),
        sa.Column("estado", sa.Text, nullable=False, server_default="pending"),
        sa.Column("origen_tipo", sa.Text, nullable=False),
        sa.Column("origen_id", sa.BigInteger, nullable=True),
        sa.Column("descripcion", sa.Text, nullable=True),
        sa.Column("idempotency_key", sa.Text, nullable=True),
        sa.Column("reverso_de", sa.BigInteger, sa.ForeignKey("journal_entry.id"), nullable=True),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("posted_en", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_journal_entry_idempotency_key"),
        sa.CheckConstraint("estado IN ('pending','posted')", name="ck_journal_entry_estado"),
    )
    op.create_index("ix_journal_entry_origen", "journal_entry", ["origen_tipo", "origen_id"])
    op.create_index("ix_journal_entry_fecha", "journal_entry", ["fecha"])


def downgrade() -> None:
    op.drop_index("ix_journal_entry_fecha", table_name="journal_entry")
    op.drop_index("ix_journal_entry_origen", table_name="journal_entry")
    op.drop_table("journal_entry")
