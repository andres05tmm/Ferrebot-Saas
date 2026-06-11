"""Log durable de recordatorios de cobranza → métrica "pesos recuperados" (ADR 0015, M-agente).

`cobranza_clientes.ultimo_recordatorio_en` se RESETEA al cerrar el ciclo (el cliente pagó), así que
no sirve para medir cuánto recuperó el agente. Este log es append-only: una fila por recordatorio
enviado (con snapshot del saldo al momento). La métrica = abonos de fiados posteriores a un
recordatorio del mismo cliente dentro de la ventana de atribución.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0018_cobranza_log
Revises: 0017_cobranza
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_cobranza_log"
down_revision: str | None = "0017_cobranza"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cobranza_recordatorios",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_id", sa.BigInteger, nullable=False),
        sa.Column("telefono", sa.Text, nullable=False),
        sa.Column("saldo", sa.Numeric(12, 2), nullable=False),   # snapshot al enviar (auditoría)
        sa.Column(
            "enviado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    # Consulta de la métrica: recordatorios de un cliente en una ventana.
    op.create_index(
        "ix_cobranza_recordatorios_cliente_enviado",
        "cobranza_recordatorios", ["cliente_id", "enviado_en"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_cobranza_recordatorios_cliente_enviado", table_name="cobranza_recordatorios"
    )
    op.drop_table("cobranza_recordatorios")
