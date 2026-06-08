"""Pack FAQ / conocimiento — tabla de conocimiento del negocio (capacidad transversal).

Crea `conocimiento` en el árbol TENANT (datos de negocio, no control): entradas de conocimiento
(titulo, contenido, activo, orden) que nutren al agente para responder dudas generales del negocio
(ubicación, horarios, precios, formas de pago, parqueo, políticas…). La recuperación v1 es por
palabras clave en Python; embeddings/pgvector (RAG real) es v2 — esta tabla no los necesita aún.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0012_faq_conocimiento
Revises: 0011_reconfirmacion
Create Date: 2026-06-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_faq_conocimiento"
down_revision: str | None = "0011_reconfirmacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conocimiento",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("titulo", sa.Text, nullable=False),
        sa.Column("contenido", sa.Text, nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("orden", sa.Integer, nullable=False, server_default="0"),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("actualizado_en", sa.TIMESTAMP(timezone=True)),
    )
    # Listado del dashboard y recuperación: activas primero, por orden.
    op.create_index("ix_conocimiento_activo_orden", "conocimiento", ["activo", "orden"])


def downgrade() -> None:
    op.drop_table("conocimiento")
