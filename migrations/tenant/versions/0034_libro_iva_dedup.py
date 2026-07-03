"""Dedup del Libro IVA para la consolidación idempotente por período (ADR 0027).

La 0001 creó `libro_iva` como libro append-only sin clave de dedup. La consolidación (ADR 0027)
materializa un renglón por documento con `referencia` = 'venta:{id}' / 'compra_fiscal:{id}'; este índice
UNIQUE PARCIAL (WHERE referencia IS NOT NULL) permite el UPSERT (ON CONFLICT) para que reprocesar un
período ACTUALICE en el lugar en vez de duplicar. Renglones históricos con `referencia` NULL quedan
intactos (el parcial no los toca).

Revision ID: 0034_libro_iva_dedup
Revises: 0033_retenciones_documento
Create Date: 2026-07-03
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0034_libro_iva_dedup"
down_revision: str | None = "0033_retenciones_documento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDICE = "uq_libro_iva_referencia"


def upgrade() -> None:
    op.execute(
        f"CREATE UNIQUE INDEX {_INDICE} ON libro_iva (referencia) WHERE referencia IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDICE}")
