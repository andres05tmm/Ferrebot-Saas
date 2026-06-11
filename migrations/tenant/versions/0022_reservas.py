"""Pack reservas (plan §2.7): el motor de agenda con otra cara — recursos `habitacion` + noches.

No crea tablas: una reserva ES una cita (inicio = check-in, fin = check-out) sobre un recurso tipo
`habitacion`. Agrega:

- `recurso_tipo` += 'habitacion' (ADD VALUE, autocommit — patrón 0016_fe_estado_anulada).
- `agenda_config.checkin_hora` (default 15:00) / `checkout_hora` (default 12:00): las horas que
  convierten "noches" en el intervalo [check-in, check-out) de la cita.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0022_reservas
Revises: 0021_cobros
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_reservas"
down_revision: str | None = "0021_cobros"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADD VALUE no corre dentro de una transacción → autocommit (mismo patrón que 0016).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE recurso_tipo ADD VALUE IF NOT EXISTS 'habitacion'")
    op.add_column(
        "agenda_config",
        sa.Column("checkin_hora", sa.Time, nullable=False, server_default="15:00"),
    )
    op.add_column(
        "agenda_config",
        sa.Column("checkout_hora", sa.Time, nullable=False, server_default="12:00"),
    )


def downgrade() -> None:
    op.drop_column("agenda_config", "checkout_hora")
    op.drop_column("agenda_config", "checkin_hora")
    # Quitar un valor de un ENUM en PG es costoso (recrear el tipo); 'habitacion' queda (inofensivo).
