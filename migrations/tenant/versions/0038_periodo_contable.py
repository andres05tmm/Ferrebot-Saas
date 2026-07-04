"""Períodos contables mensuales con candado (ADR 0030, motor contable).

`estado`: open (acepta postings) | locked | closed (los rechazan). Clave natural (anio, mes). El
servicio resuelve/crea el período `open` de la fecha del asiento y rechaza el posting si está
locked/closed (invariante crítico "período bloqueado rechaza posting").

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0038_periodo_contable
Revises: 0037_puc_cuentas
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_periodo_contable"
down_revision: str | None = "0037_puc_cuentas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "periodo_contable",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("anio", sa.SmallInteger, nullable=False),
        sa.Column("mes", sa.SmallInteger, nullable=False),
        sa.Column("estado", sa.Text, nullable=False, server_default="open"),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("anio", "mes", name="uq_periodo_contable_anio_mes"),
        sa.CheckConstraint(
            "estado IN ('open','locked','closed')", name="ck_periodo_contable_estado"
        ),
        sa.CheckConstraint("mes BETWEEN 1 AND 12", name="ck_periodo_contable_mes"),
    )


def downgrade() -> None:
    op.drop_table("periodo_contable")
