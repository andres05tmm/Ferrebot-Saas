"""config_empresa: overrides de configuración no secreta por empresa.

Slice mínimo (clave/valor texto) que habilita el override por tenant del proveedor/modelo LLM
(`llm_provider`, `llm_model_worker`, `llm_model_orquestador`) y, a futuro, otros umbrales por
empresa (p.ej. monto/confirmación del bypass). Los SECRETOS siguen yendo cifrados en
`secretos_empresa`; esto es solo config en claro.

Revision ID: 0002_config_empresa
Revises: 0001_control
Create Date: 2026-06-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_config_empresa"
down_revision: str | None = "0001_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "config_empresa",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("empresa_id", sa.BigInteger, sa.ForeignKey("empresas.id"), nullable=False),
        sa.Column("clave", sa.Text, nullable=False),
        sa.Column("valor", sa.Text, nullable=False),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("empresa_id", "clave", name="uq_config_empresa_clave"),
    )


def downgrade() -> None:
    op.drop_table("config_empresa")
