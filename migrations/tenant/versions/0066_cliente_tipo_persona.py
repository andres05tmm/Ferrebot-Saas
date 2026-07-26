"""Tipo de persona del cliente (natural / jurídica) como dato propio.

Hasta ahora el dashboard lo DERIVABA del tipo de documento (NIT → jurídica), pero el dato real no
siempre coincide: en el catálogo migrado hay S.A.S cargadas con CC. `clientes.tipo_persona` guarda
lo que el usuario elige; NULL = sin dato → el dashboard sigue derivándolo (backward-compatible,
ningún cliente existente cambia de comportamiento).

Aditivo (columna NULL). Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0066_cliente_tipo_persona
Revises: 0065_perfil_usuario
Create Date: 2026-07-25
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_cliente_tipo_persona"
down_revision: str | None = "0065_perfil_usuario"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("clientes", sa.Column("tipo_persona", sa.Text))


def downgrade() -> None:
    op.drop_column("clientes", "tipo_persona")
