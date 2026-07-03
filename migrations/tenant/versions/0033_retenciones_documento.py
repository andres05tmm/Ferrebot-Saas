"""Renglones de retención/INC por documento (ADR 0027).

Cada fila es un tributo CALCULADO para un documento (venta o compra): tipo, concepto, base, tarifa y
valor. La clave natural (doc_tipo, doc_id, tipo, concepto) hace idempotente reaplicar el motor sobre el
mismo documento (UPSERT en el lugar, sin duplicar). NO altera los totales del documento: el neto a
recibir/pagar se deriva restando estos valores. Tabla de negocio sin `empresa_id`.

Revision ID: 0033_retenciones_documento
Revises: 0032_config_retenciones
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_retenciones_documento"
down_revision: str | None = "0032_config_retenciones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retenciones_documento",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("doc_tipo", sa.Text, nullable=False),   # 'venta' | 'compra'
        sa.Column("doc_id", sa.BigInteger, nullable=False),
        sa.Column("tipo", sa.Text, nullable=False),       # 'retefuente'|'ica'|'reteiva'|'inc'
        sa.Column("concepto", sa.Text, nullable=False),
        sa.Column("base", sa.Numeric(12, 2), nullable=False),
        sa.Column("tarifa", sa.Numeric(9, 4), nullable=False),
        sa.Column("valor", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "doc_tipo", "doc_id", "tipo", "concepto", name="uq_retenciones_documento_doc"
        ),
    )
    op.create_index(
        "ix_retenciones_documento_doc", "retenciones_documento", ["doc_tipo", "doc_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_retenciones_documento_doc", table_name="retenciones_documento")
    op.drop_table("retenciones_documento")
