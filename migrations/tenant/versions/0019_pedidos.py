"""Pack pedidos (ADR 0016): pedidos y domicilios por WhatsApp sobre el catálogo del POS.

No toca `productos`/`inventario` (el menú ES el catálogo, solo se LEE); agrega el plano del pedido:

- `pedido_config` (una fila): horario de cocina, mínimo de pedido, tiempo estimado, domicilio default.
- `zonas_domicilio`: barrio → tarifa.
- `pedidos` + `pedido_items`: el ciclo `recibido → confirmado → en_preparacion → en_camino →
  entregado | cancelado`, con snapshot de nombre/precio por ítem e idempotencia por key.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0019_pedidos
Revises: 0018_cobranza_log
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019_pedidos"
down_revision: str | None = "0018_cobranza_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ESTADOS = ("recibido", "confirmado", "en_preparacion", "en_camino", "entregado", "cancelado")


def upgrade() -> None:
    valores = ", ".join(f"'{v}'" for v in _ESTADOS)
    op.execute(f"CREATE TYPE pedido_estado AS ENUM ({valores})")

    op.create_table(
        "pedido_config",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("hora_apertura", sa.Time, nullable=False, server_default="08:00"),
        sa.Column("hora_cierre", sa.Time, nullable=False, server_default="21:00"),
        sa.Column("minimo_pedido", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("tiempo_estimado_min", sa.Integer, nullable=False, server_default="45"),
        sa.Column("costo_domicilio_default", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )

    op.create_table(
        "zonas_domicilio",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("tarifa", sa.Numeric(12, 2), nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "pedidos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_nombre", sa.Text),
        sa.Column("cliente_telefono", sa.Text, nullable=False),
        sa.Column("direccion", sa.Text),
        sa.Column("zona_id", sa.BigInteger),
        sa.Column("costo_domicilio", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("metodo_pago", sa.Text),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADOS, name="pedido_estado", create_type=False),
            nullable=False, server_default="recibido",
        ),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("notas", sa.Text),
        sa.Column("origen", sa.Text, nullable=False, server_default="whatsapp"),
        sa.Column("idempotency_key", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    # Kanban del dashboard: pedidos por (estado, creado_en); y el borrador/estado del que escribe.
    op.create_index("ix_pedidos_estado_creado", "pedidos", ["estado", "creado_en"])
    op.create_index("ix_pedidos_telefono", "pedidos", ["cliente_telefono"])
    op.create_index(
        "uq_pedidos_idempotency_key", "pedidos", ["idempotency_key"],
        unique=True, postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "pedido_items",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "pedido_id", sa.BigInteger,
            sa.ForeignKey("pedidos.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("producto_id", sa.BigInteger),
        sa.Column("nombre", sa.Text, nullable=False),         # snapshot al momento del pedido
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
        sa.Column("precio_unitario", sa.Numeric(12, 2), nullable=False),  # snapshot
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_pedido_items_pedido", "pedido_items", ["pedido_id"])


def downgrade() -> None:
    op.drop_index("ix_pedido_items_pedido", table_name="pedido_items")
    op.drop_table("pedido_items")
    op.drop_index("uq_pedidos_idempotency_key", table_name="pedidos")
    op.drop_index("ix_pedidos_telefono", table_name="pedidos")
    op.drop_index("ix_pedidos_estado_creado", table_name="pedidos")
    op.drop_table("pedidos")
    op.drop_table("zonas_domicilio")
    op.drop_table("pedido_config")
    op.execute("DROP TYPE pedido_estado")
