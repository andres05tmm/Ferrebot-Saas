"""Saldos cacheados por cuenta/período (ADR 0030, motor contable).

Materialización recomputable desde `journal_line` (patrón Square Books): débitos/creditos acumulados
y `saldo` con signo según la naturaleza de la cuenta. Clave natural (cuenta_id, periodo_id). Es una
CACHÉ: siempre reconstruible con `recomputar_saldos`; su verdad son las líneas.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0041_saldo_cache
Revises: 0040_journal_line
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_saldo_cache"
down_revision: str | None = "0040_journal_line"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saldo_cache",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cuenta_id", sa.BigInteger, sa.ForeignKey("puc_cuentas.id"), nullable=False),
        sa.Column(
            "periodo_id", sa.BigInteger, sa.ForeignKey("periodo_contable.id"), nullable=False
        ),
        sa.Column("debitos", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("creditos", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("saldo", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("cuenta_id", "periodo_id", name="uq_saldo_cache_cuenta_periodo"),
    )


def downgrade() -> None:
    op.drop_table("saldo_cache")
