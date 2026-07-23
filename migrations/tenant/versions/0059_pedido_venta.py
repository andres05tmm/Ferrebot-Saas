"""Puente pedido → venta (F1 Pack Restaurante, ADR 0032; cierra el "v2" del ADR 0016).

`pedidos.venta_id` (FK a ventas, UNIQUE, NULL) es el vínculo idempotente del puente — patrón
calcado de `citas.venta_id` (ADR 0022 D3): se escribe en la misma transacción que la venta con el
pedido tomado bajo FOR UPDATE. `convertido_en` deja la traza del momento contable.

Aditivo y NULL-safe (el esquema es compartido por todos los verticales). Se aplica a TODAS las
empresas vía `tools.migrate_tenants`.

Revision ID: 0059_pedido_venta
Revises: 0058_pedido_telefono_contacto
Create Date: 2026-07-23
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0059_pedido_venta"
down_revision: str | None = "0058_pedido_telefono_contacto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pedidos", sa.Column("venta_id", sa.BigInteger, nullable=True))
    op.add_column("pedidos", sa.Column("convertido_en", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_pedidos_venta_id", "pedidos", "ventas", ["venta_id"], ["id"], ondelete="SET NULL"
    )
    op.create_unique_constraint("uq_pedidos_venta_id", "pedidos", ["venta_id"])


def downgrade() -> None:
    op.drop_constraint("uq_pedidos_venta_id", "pedidos", type_="unique")
    op.drop_constraint("fk_pedidos_venta_id", "pedidos", type_="foreignkey")
    op.drop_column("pedidos", "convertido_en")
    op.drop_column("pedidos", "venta_id")
