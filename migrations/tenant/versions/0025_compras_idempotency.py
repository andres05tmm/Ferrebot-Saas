"""idempotency_key en compras — idempotencia ESTRUCTURAL del registro de compra.

`registrar_compra` (ai-tools.md §4) debe ser idempotente como el resto de operaciones que mueven
stock/dinero, pero `compras` era la única tabla ancla sin `idempotency_key`. Aquí se agrega la columna
+ índice UNIQUE PARCIAL (WHERE NOT NULL), mismo patrón estructural que la 0002 (movimientos_inventario)
y la 0003 (caja/gastos/fiados). Así un reintento del bot, de la cola offline o de un webhook con la
misma key NO duplica la compra (ni sus ENTRADAS de inventario).

Seguro sobre Punto Rojo: las compras existentes quedan con key NULL y no chocan con el índice parcial
(Postgres trata cada NULL como distinto, y el índice solo cubre filas con key).

Revision ID: 0025_compras_idem
Revises: 0024_conversacion_mensajes
Create Date: 2026-06-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_compras_idem"
down_revision: str | None = "0024_conversacion_mensajes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDICE = "uq_compras_idempotency_key"


def upgrade() -> None:
    op.add_column("compras", sa.Column("idempotency_key", sa.Text, nullable=True))
    op.execute(
        f"CREATE UNIQUE INDEX {_INDICE} ON compras (idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDICE}")
    op.drop_column("compras", "idempotency_key")
