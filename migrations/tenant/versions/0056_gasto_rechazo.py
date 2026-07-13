"""Rechazo en la bandeja de revisión de gastos (F2.2 del rediseño PIM).

Un recibo que el bot leyó mal ya movió caja al crearse (el gasto postea SU egreso en la misma tx).
Hasta ahora la bandeja solo podía APROBAR: el egreso errado quedaba para siempre. El rechazo lo anula
con un movimiento de caja INVERSO (ingreso por el monto exacto — nunca delete: regla "nada mueve caja
sin movimiento") y marca el gasto:

  - `gastos.anulado_en` TIMESTAMPTZ NULL — instante del rechazo; NULL = gasto vivo. Es también el ancla
    de idempotencia del rechazo (re-rechazar es replay). Los lectores de gastos (listados, gasto real de
    obra, libros, conciliación, proyector contable) filtran `anulado_en IS NULL`.
  - `gastos.motivo_rechazo` TEXT NULL — opcional, lo escribe el admin al rechazar.

Solo ALTER aditivo (backward-compatible). Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0056_gasto_rechazo
Revises: 0055_operacion_maquina_vivo
Create Date: 2026-07-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0056_gasto_rechazo"
down_revision: str | None = "0055_operacion_maquina_vivo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gastos", sa.Column("anulado_en", sa.DateTime(timezone=True), nullable=True))
    op.add_column("gastos", sa.Column("motivo_rechazo", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("gastos", "motivo_rechazo")
    op.drop_column("gastos", "anulado_en")
