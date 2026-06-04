"""UNIQUE(tipo, clave) en `memoria_entidades` → habilita el upsert idempotente del scratch del bot.

La tabla la creó la 0001 sin restricción; el orquestador del turno (entregable 4) recuerda el último
cliente/producto por chat con `ON CONFLICT (tipo, clave) DO UPDATE`, que requiere este UNIQUE. No
toca filas existentes (Postgres permite varios NULL bajo UNIQUE; las filas reales traen tipo+clave).

Revision ID: 0004_memoria_uq
Revises: 0003_dinero_idem
Create Date: 2026-06-04
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004_memoria_uq"
down_revision: str | None = "0003_dinero_idem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "uq_memoria_entidades_tipo_clave"


def upgrade() -> None:
    op.create_unique_constraint(_CONSTRAINT, "memoria_entidades", ["tipo", "clave"])


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "memoria_entidades", type_="unique")
