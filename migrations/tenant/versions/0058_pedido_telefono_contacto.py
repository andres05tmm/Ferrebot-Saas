"""Teléfono de contacto del pedido a domicilio (demo Siriuss).

En Telegram la identidad del cliente es un ID opaco (`tg:{chat_id}`): el domiciliario no tiene a
quién llamar. El agente pide el teléfono REAL de contacto al confirmar y aquí se persiste como
dato del pedido (separado de `cliente_telefono`, que sigue siendo la identidad del canal y JAMÁS
viene del modelo). En WhatsApp el default es el propio número del cliente.

Aditivo (una columna NULL). Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0058_pedido_telefono_contacto
Revises: 0057_comprobantes_pago
Create Date: 2026-07-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_pedido_telefono_contacto"
down_revision: str | None = "0057_comprobantes_pago"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pedidos", sa.Column("telefono_contacto", sa.Text))


def downgrade() -> None:
    op.drop_column("pedidos", "telefono_contacto")
