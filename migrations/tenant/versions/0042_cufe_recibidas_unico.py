"""Recepción de facturas de proveedor por QR (ADR 0020, F1): idempotencia por CUFE.

El `cufe_proveedor` de una compra fiscal pasa a ser ÚNICO por tenant (hoy nullable y no único). Es el
ancla de idempotencia de la recepción por QR: reimportar el mismo CUFE devuelve el registro existente
(200), no crea uno nuevo ni duplica la cuenta por pagar. Índice UNIQUE (no NOT NULL): Postgres admite
múltiples NULL, así que las compras fiscales SIN CUFE (derivadas de una compra normal, Slice 6a) siguen
conviviendo sin colisionar. La base ES la frontera del tenant (sin `empresa_id`).

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0042_cufe_recibidas_unico
Revises: 0041_saldo_cache
Create Date: 2026-07-04
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0042_cufe_recibidas_unico"
down_revision: str | None = "0041_saldo_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDICE = "uq_compras_fiscal_cufe_proveedor"


def upgrade() -> None:
    # UNIQUE (no NOT NULL): dedup por CUFE dejando pasar las fiscales sin CUFE (múltiples NULL en PG).
    op.create_index(_INDICE, "compras_fiscal", ["cufe_proveedor"], unique=True)


def downgrade() -> None:
    op.drop_index(_INDICE, table_name="compras_fiscal")
