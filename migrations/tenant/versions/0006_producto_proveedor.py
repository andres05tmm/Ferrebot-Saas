"""Rediseño de producto: `precio_mayorista`→`precio_especial`, `marca`→`proveedor_id` (FK proveedores).

- RENOMBRA `productos.precio_mayorista` → `precio_especial` (mismo tipo, sin pérdida de datos).
- REEMPLAZA `productos.marca` (TEXT, hoy todo NULL) por `productos.proveedor_id` (BIGINT, NULLABLE) con
  FK a `proveedores.id` (ON DELETE SET NULL): el proveedor se elige de la lista registrada, no es texto.

`marca` se dropea sin respaldo porque está 100% en NULL (nunca se pobló). Reversible: el downgrade
recrea `marca` (vacía) y revierte el rename.

Revision ID: 0006_producto_proveedor
Revises: 0005_drop_config_empresa
Create Date: 2026-06-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_producto_proveedor"
down_revision: str | None = "0005_drop_config_empresa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FK = "fk_productos_proveedor_id"


def upgrade() -> None:
    op.alter_column("productos", "precio_mayorista", new_column_name="precio_especial")
    op.drop_column("productos", "marca")
    op.add_column("productos", sa.Column("proveedor_id", sa.BigInteger, nullable=True))
    op.create_foreign_key(
        _FK, "productos", "proveedores", ["proveedor_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "productos", type_="foreignkey")
    op.drop_column("productos", "proveedor_id")
    op.add_column("productos", sa.Column("marca", sa.Text, nullable=True))
    op.alter_column("productos", "precio_especial", new_column_name="precio_mayorista")
