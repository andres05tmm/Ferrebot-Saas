"""Pack ventas/cotizaciones (ADR 0017): cotizar y armar carrito por WhatsApp con el catálogo del POS.

No toca `productos`/`inventario` (solo lectura); agrega el plano de la cotización:

- `ventas_wa_config` (una fila): mostrar stock sí/no + vigencia de la cotización emitida.
- `cotizaciones` + `cotizacion_items`: carrito `abierta` (uno por teléfono) → `emitida` (con
  vigencia) → `aceptada | vencida | cancelada`. Snapshot de nombre/precio por ítem.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0020_cotizaciones
Revises: 0019_pedidos
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_cotizaciones"
down_revision: str | None = "0019_pedidos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ESTADOS = ("abierta", "emitida", "aceptada", "vencida", "cancelada")


def upgrade() -> None:
    valores = ", ".join(f"'{v}'" for v in _ESTADOS)
    op.execute(f"CREATE TYPE cotizacion_estado AS ENUM ({valores})")

    op.create_table(
        "ventas_wa_config",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("mostrar_stock", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("vigencia_dias", sa.Integer, nullable=False, server_default="3"),
    )

    op.create_table(
        "cotizaciones",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_telefono", sa.Text, nullable=False),
        sa.Column("cliente_nombre", sa.Text),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADOS, name="cotizacion_estado", create_type=False),
            nullable=False, server_default="abierta",
        ),
        sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("vigencia_hasta", sa.Date),
        sa.Column("idempotency_key", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cotizaciones_estado", "cotizaciones", ["estado", "creado_en"])
    op.create_index("ix_cotizaciones_telefono", "cotizaciones", ["cliente_telefono"])
    op.create_index(
        "uq_cotizaciones_idempotency_key", "cotizaciones", ["idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "cotizacion_items",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "cotizacion_id", sa.BigInteger,
            sa.ForeignKey("cotizaciones.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("producto_id", sa.BigInteger),
        sa.Column("nombre", sa.Text, nullable=False),          # snapshot
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
        sa.Column("precio_unitario", sa.Numeric(12, 2), nullable=False),  # snapshot (escalonado ya aplicado)
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_cotizacion_items_cotizacion", "cotizacion_items", ["cotizacion_id"])


def downgrade() -> None:
    op.drop_index("ix_cotizacion_items_cotizacion", table_name="cotizacion_items")
    op.drop_table("cotizacion_items")
    op.drop_index("uq_cotizaciones_idempotency_key", table_name="cotizaciones")
    op.drop_index("ix_cotizaciones_telefono", table_name="cotizaciones")
    op.drop_index("ix_cotizaciones_estado", table_name="cotizaciones")
    op.drop_table("cotizaciones")
    op.drop_table("ventas_wa_config")
    op.execute("DROP TYPE cotizacion_estado")
