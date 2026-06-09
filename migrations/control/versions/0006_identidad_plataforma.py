"""identidades: identidad de plataforma (super_admin) — empresa_id NULLABLE + CHECK (ADR 0010 §D2).

El super-admin (operador SaaS) es una identidad de PLATAFORMA: no pertenece a ningún tenant, así que su
`empresa_id` es NULL. Las identidades de tenant (admin/vendedor) SIEMPRE tienen empresa. Un CHECK ata la
nulabilidad al rol para que el modelo no se pueda corromper desde SQL:

    (rol = 'super_admin' AND empresa_id IS NULL) OR (rol <> 'super_admin' AND empresa_id IS NOT NULL)

Revision ID: 0006_identidad_plataforma
Revises: 0005_identidades
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_identidad_plataforma"
down_revision: str | None = "0005_identidades"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_identidades_rol_empresa"
_COND = (
    "(rol = 'super_admin' AND empresa_id IS NULL) "
    "OR (rol <> 'super_admin' AND empresa_id IS NOT NULL)"
)


def upgrade() -> None:
    # 1) Relaja empresa_id a NULLABLE (la identidad de plataforma no tiene empresa).
    op.alter_column("identidades", "empresa_id", existing_type=sa.BigInteger, nullable=True)
    # 2) CHECK que ata la nulabilidad al rol (super_admin ⇒ sin empresa; el resto ⇒ con empresa).
    op.create_check_constraint(_CHECK, "identidades", _COND)


def downgrade() -> None:
    # Quita el CHECK y vuelve empresa_id a NOT NULL. Las identidades de plataforma (empresa_id NULL) no
    # caben en el modelo previo: se eliminan para que el downgrade quede limpio (no es data de negocio).
    op.drop_constraint(_CHECK, "identidades", type_="check")
    op.execute("DELETE FROM identidades WHERE empresa_id IS NULL")
    op.alter_column("identidades", "empresa_id", existing_type=sa.BigInteger, nullable=False)
