"""webhooks_matias_recibidos: idempotencia de los webhooks de MATIAS (D7.1 del ADR 0012).

`webhook_id` (el header `X-Webhook-ID` de cada entrega) es UNIQUE: el registro en BD da la idempotencia
(MATIAS reintenta hasta 6 veces). El cuerpo se guarda íntegro (`payload` JSONB) para que el worker lo
procese; `procesado_en` marca cuándo se aplicó (auditoría / barrido de pendientes). Vive en la base del
tenant porque el evento es sobre un documento de esa empresa (la base ES la frontera, tenancy.md §4).

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0014_webhooks_recibidos
Revises: 0013_fe_xml_historico
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0014_webhooks_recibidos"
down_revision: str | None = "0013_fe_xml_historico"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhooks_matias_recibidos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("webhook_id", sa.Text, nullable=False),
        sa.Column("evento", sa.Text, nullable=False, server_default=""),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("recibido_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("procesado_en", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("webhook_id", name="uq_webhooks_matias_recibidos_id"),
    )


def downgrade() -> None:
    op.drop_table("webhooks_matias_recibidos")
