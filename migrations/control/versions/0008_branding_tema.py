"""branding_tema: tema visual con nombre por empresa (white-label).

Añade `branding.tema` (TEXT, nullable) para que cada empresa pueda declarar un tema de UI con nombre
(p. ej. "aurora") que el dashboard aplica como bloque de CSS vars (`data-tema`). Default seguro: NULL
→ el dashboard cae al tema base (rojo #C8200E de siempre). No es un secreto ni cruza datos de negocio:
es branding, vive en el control DB junto a logo/color/nombre (control_repo.leer_branding).

Revision ID: 0008_branding_tema
Revises: 0007_webhooks_matias
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_branding_tema"
down_revision: str | None = "0007_webhooks_matias"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("branding", sa.Column("tema", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("branding", "tema")
