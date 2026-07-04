"""PUC: árbol de cuentas del Plan Único de Cuentas por tenant (ADR 0030, motor contable).

Árbol con `parent_id` (auto-FK), código único, `naturaleza` (debito/credito) e `imputable` (solo las
hojas reciben movimientos). La semilla del PUC colombiano NO va en la migración: se siembra opt-in
por el servicio (`asegurar_puc`) al habilitar la feature `contabilidad_ledger`, para no inflar la base
de tenants que no usan el ledger. Tabla de negocio SIN `empresa_id` (la base ES la frontera del tenant).

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0037_puc_cuentas
Revises: 0036_gastos_cxp
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_puc_cuentas"
down_revision: str | None = "0036_gastos_cxp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "puc_cuentas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("codigo", sa.Text, nullable=False),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("parent_id", sa.BigInteger, sa.ForeignKey("puc_cuentas.id"), nullable=True),
        sa.Column("naturaleza", sa.Text, nullable=False),
        sa.Column("imputable", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("codigo", name="uq_puc_cuentas_codigo"),
        sa.CheckConstraint("naturaleza IN ('debito','credito')", name="ck_puc_cuentas_naturaleza"),
    )


def downgrade() -> None:
    op.drop_table("puc_cuentas")
