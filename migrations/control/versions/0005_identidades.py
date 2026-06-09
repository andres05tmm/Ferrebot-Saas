"""identidades: directorio global de login en el control DB (ADR 0009 §D1).

Login email/contraseña sobre link compartido: el tenant se resuelve DESDE el usuario. La fila ES el
enlace `email → (empresa_id, usuario_id, rol)`; el login lee solo el control DB (no toca la base del
tenant). Guarda solo datos de auth/ruteo, no de negocio (multitenancy.md §3). `password_hash` es
nullable: la identidad puede existir sin contraseña aún (pendiente de set-password). `usuario_id` es
el id del usuario DENTRO de la base de su tenant (materializado).

Email único case-insensitive vía índice funcional `lower(email)` (la capa normaliza a minúsculas).

Revision ID: 0005_identidades
Revises: 0004_grandfather_pos
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_identidades"
down_revision: str | None = "0004_grandfather_pos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identidades",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.Text, nullable=False),
        # nullable: identidad creada sin contraseña (set-password pendiente). Nunca clave en claro.
        sa.Column("password_hash", sa.Text),
        sa.Column(
            "empresa_id", sa.BigInteger,
            sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False,
        ),
        # id del usuario en la base de SU tenant (materializado): de aquí salen sub/rol del JWT.
        sa.Column("usuario_id", sa.BigInteger, nullable=False),
        sa.Column("rol", sa.Text, nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("actualizado_en", sa.TIMESTAMP(timezone=True)),
    )
    # Único por email case-insensitive (la capa normaliza a minúsculas; el índice lo blinda igual).
    op.create_index("uq_identidades_email", "identidades", [sa.text("lower(email)")], unique=True)
    op.create_index("ix_identidades_empresa", "identidades", ["empresa_id"])


def downgrade() -> None:
    op.drop_table("identidades")   # arrastra sus índices
