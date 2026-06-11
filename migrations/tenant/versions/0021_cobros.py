"""Frente de pagos (ADR 0013): solicitudes de cobro (link/QR Bre-B vía PSP o etiqueta manual).

Infraestructura TRANSVERSAL (como facturación): los packs la consumen. `cobros` registra cada
solicitud de cobro con su origen de dominio (pedido/cita/cobranza/manual), el proveedor (bold |
manual) y el ciclo `pendiente → pagado | vencido | cancelado`. El dinero va a la cuenta del
NEGOCIO (su PSP); la plataforma jamás toca el flujo de plata.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0021_cobros
Revises: 0020_cotizaciones
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_cobros"
down_revision: str | None = "0020_cotizaciones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ESTADOS = ("pendiente", "pagado", "vencido", "cancelado")


def upgrade() -> None:
    valores = ", ".join(f"'{v}'" for v in _ESTADOS)
    op.execute(f"CREATE TYPE cobro_estado AS ENUM ({valores})")

    op.create_table(
        "cobros",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("referencia", sa.Text, nullable=False, unique=True),   # nuestra llave idempotente
        sa.Column("origen", sa.Text, nullable=False),                    # pedido | cita | cobranza | manual
        sa.Column("origen_id", sa.BigInteger),
        sa.Column("cliente_telefono", sa.Text),
        sa.Column("monto", sa.Numeric(12, 2), nullable=False),
        sa.Column("descripcion", sa.Text),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADOS, name="cobro_estado", create_type=False),
            nullable=False, server_default="pendiente",
        ),
        sa.Column("proveedor", sa.Text, nullable=False, server_default="manual"),  # bold | manual
        sa.Column("proveedor_id", sa.Text),     # payment_link del PSP (LNK_xxx)
        sa.Column("url", sa.Text),              # link de pago para el cliente (NULL en manual)
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    # Conciliación: cobros pendientes de un proveedor; y un cobro por origen de dominio (no duplicar).
    op.create_index("ix_cobros_estado_proveedor", "cobros", ["estado", "proveedor"])
    op.create_index(
        "uq_cobros_origen", "cobros", ["origen", "origen_id"],
        unique=True, postgresql_where=sa.text("origen_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_cobros_origen", table_name="cobros")
    op.drop_index("ix_cobros_estado_proveedor", table_name="cobros")
    op.drop_table("cobros")
    op.execute("DROP TYPE cobro_estado")
