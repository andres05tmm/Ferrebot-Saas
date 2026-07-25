"""Perfil de usuario en el dashboard (foto + color) y atribución de abonos de fiados.

- `usuarios.avatar_url` / `usuarios.color`: personalización del perfil (foto en Cloudinary por
  empresa, color de acento elegido por la persona).
- `fiados_movimientos.usuario_id`: QUIÉN registró el abono. Ventas (`vendedor_id`), gastos
  (`usuario_id`) y compras (movimientos ENTRADA) ya atribuyen a la persona; los abonos eran el
  hueco del historial de acciones del perfil. Histórico queda NULL (no atribuible).

Aditivo (columnas NULL). Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0065_perfil_usuario
Revises: 0064_trabajos_impresion
Create Date: 2026-07-25
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_perfil_usuario"
down_revision: str | None = "0064_trabajos_impresion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("usuarios", sa.Column("avatar_url", sa.Text))
    op.add_column("usuarios", sa.Column("color", sa.Text))
    op.add_column("fiados_movimientos", sa.Column("usuario_id", sa.BigInteger))


def downgrade() -> None:
    op.drop_column("fiados_movimientos", "usuario_id")
    op.drop_column("usuarios", "color")
    op.drop_column("usuarios", "avatar_url")
