"""fe_estado += 'anulada': anulación DIAN confirmada (document.voided).

`ALTER TYPE ... ADD VALUE` NO corre dentro de una transacción → migración DEDICADA y en `autocommit_block`
(mismo patrón que 0015_fe_tipo_pos). Hasta F2.1 `document.voided` solo se anotaba en `dian_respuesta`
porque el enum no tenía un estado dedicado; ahora la anulación es un estado terminal observable
(`aceptada → anulada`), con su evento SSE `factura_anulada`.

NO se toca el valor reservado `enviada` (quitar un valor de un enum en PG es costoso): la emisión va
`pendiente → aceptada | rechazada | error` de forma síncrona; `enviada` queda reservado para un futuro
modelo de aceptación confirmada por webhook (requeriría su propio ADR). Ver `docs/facturacion-dian.md`.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7). Idempotente: solo agrega el
valor si falta (re-aplicar no rompe).

Revision ID: 0016_fe_estado_anulada
Revises: 0015_fe_tipo_pos
Create Date: 2026-06-10
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0016_fe_estado_anulada"
down_revision: str | None = "0015_fe_tipo_pos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD VALUE no es transaccional: fuera del bloque transaccional de Alembic. IF NOT EXISTS lo hace
    # idempotente. Un enum no permite quitar valores → el downgrade es un no-op (ver abajo).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fe_estado ADD VALUE IF NOT EXISTS 'anulada'")


def downgrade() -> None:
    # PostgreSQL no soporta quitar un valor de un ENUM; el downgrade es intencionalmente un no-op
    # (revertir exigiría recrear el tipo y reescribir la columna; no vale el riesgo para un valor inerte).
    pass
