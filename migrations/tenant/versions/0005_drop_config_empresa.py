"""Drop de `config_empresa` vestigial en la app DB del tenant.

La 0001 creó `config_empresa (clave, valor)` en cada app DB, pero la config no-secreta por empresa
vive en el CONTROL DB (control 0002, con `empresa_id`): los lectores (`core/llm/stores.py`,
`modules/facturacion/config.py`, umbrales en `ai/ports.py`) consultan `WHERE empresa_id = :e`,
columna que la tabla del tenant no tiene. Nadie la lee → se elimina. El único escritor era el seed de
`tools/provision_tenant.py` (clave `iva_incluido_en_precio` que nadie leía), retirado en este cambio.

Revision ID: 0005_drop_config_empresa
Revises: 0004_memoria_uq
Create Date: 2026-06-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_drop_config_empresa"
down_revision: str | None = "0004_memoria_uq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("config_empresa")


def downgrade() -> None:
    # Recrea la tabla tal como la dejó la 0001 (clave TEXT PK, valor JSONB).
    op.create_table(
        "config_empresa",
        sa.Column("clave", sa.Text, primary_key=True),
        sa.Column("valor", postgresql.JSONB),
    )
