"""gmail_cuentas: registro por empresa del buzón Gmail de ingesta (Bancolombia hoy; D2 compras luego).

Vive en el CONTROL DB (no en la base del tenant) porque su estado operativo — el `last_history_id`
procesado y `watch_expira` — lo barre el worker (cron de renovación del watch) SIN abrir cada tenant, y
porque el `webhook_token` mapea un push global de Pub/Sub → empresa (mismo patrón que MATIAS). El
refresh_token OAuth NO va aquí: es un secreto, va cifrado en `secretos_empresa`.

`proposito` separa buzones ('bancolombia' | 'compras'): una empresa puede tener uno de cada uno.

Revision ID: 0010_gmail_cuentas
Revises: 0009_branding_preset
Create Date: 2026-07-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_gmail_cuentas"
down_revision: str | None = "0009_branding_preset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gmail_cuentas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("empresa_id", sa.BigInteger,
                  sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposito", sa.Text, nullable=False, server_default="bancolombia"),
        sa.Column("email", sa.Text, nullable=True),
        sa.Column("webhook_token", sa.Text, nullable=False, unique=True),
        sa.Column("pubsub_topic", sa.Text, nullable=True),
        sa.Column("last_history_id", sa.Text, nullable=True),
        sa.Column("watch_expira", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        # Una empresa tiene a lo sumo un buzón por propósito.
        sa.UniqueConstraint("empresa_id", "proposito", name="uq_gmail_cuenta_empresa_proposito"),
    )


def downgrade() -> None:
    op.drop_table("gmail_cuentas")
