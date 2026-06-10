"""fe_tipo += 'pos': documento equivalente POS electrónico (ADR 0012 D3).

`ALTER TYPE ... ADD VALUE` NO corre dentro de una transacción → migración DEDICADA y en `autocommit_block`
(ADR 0012 D3). Reusa `facturas_electronicas` con `tipo='pos'`: misma máquina de estados, idempotencia,
eventos SSE, historial y archivado del XML. El consecutivo/prefijo del POS los asigna MATIAS por
autoincremento (D4), así que la fila `pos` puede nacer con `consecutivo`/`prefijo` NULL (ya nullable).

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7). Idempotente: solo agrega el
valor si falta (re-aplicar no rompe).

Revision ID: 0015_fe_tipo_pos
Revises: 0014_webhooks_recibidos
Create Date: 2026-06-09
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0015_fe_tipo_pos"
down_revision: str | None = "0014_webhooks_recibidos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD VALUE no es transaccional: fuera del bloque transaccional de Alembic. IF NOT EXISTS lo hace
    # idempotente. Un enum no permite quitar valores → el downgrade es un no-op (ver abajo).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fe_tipo ADD VALUE IF NOT EXISTS 'pos'")


def downgrade() -> None:
    # PostgreSQL no soporta quitar un valor de un ENUM; el downgrade es intencionalmente un no-op
    # (revertir exigiría recrear el tipo y reescribir la columna; no vale el riesgo para un valor inerte).
    pass
