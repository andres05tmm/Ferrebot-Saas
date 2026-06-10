"""webhooks_matias: registro del webhook de MATIAS por empresa (D7.1 del ADR 0012).

Resuelve la empresa de un webhook entrante por el TOKEN de la ruta (`/webhooks/matias/{token}`), NUNCA
por el payload (tenancy.md §1). Una fila por empresa: `token` único (clave de resolución) + la URL de
callback registrada en MATIAS. El SECRET de la firma NO va aquí: va CIFRADO en `secretos_empresa`
(clave `matias_webhook_secret`), como el resto de secretos por empresa (security.md).

Revision ID: 0007_webhooks_matias
Revises: 0006_identidad_plataforma
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_webhooks_matias"
down_revision: str | None = "0006_identidad_plataforma"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhooks_matias",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("empresa_id", sa.BigInteger, sa.ForeignKey("empresas.id"), nullable=False),
        sa.Column("token", sa.Text, nullable=False),
        sa.Column("callback_url", sa.Text, nullable=False),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("empresa_id", name="uq_webhooks_matias_empresa"),
        sa.UniqueConstraint("token", name="uq_webhooks_matias_token"),
    )


def downgrade() -> None:
    op.drop_table("webhooks_matias")
