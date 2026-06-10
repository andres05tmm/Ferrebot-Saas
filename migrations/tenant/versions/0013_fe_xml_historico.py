"""facturas_electronicas.xml_contenido: archivado del XML técnico (histórico fiscal 5 años, D7.3).

Prerrequisito del ADR 0012 que aplica YA a la FE actual: el job post-aceptada descarga el XML de
MATIAS (`GET /documents/xml/{trackId}`) y lo persiste en la base del tenant, además de poblar
`xml_url`/`pdf_url` (que existían pero nunca se poblaban). Texto nullable: las facturas previas
quedan sin XML hasta que el reconciliador/archivador las barra.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0013_fe_xml_historico
Revises: 0012_faq_conocimiento
Create Date: 2026-06-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_fe_xml_historico"
down_revision: str | None = "0012_faq_conocimiento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("facturas_electronicas", sa.Column("xml_contenido", sa.Text))


def downgrade() -> None:
    op.drop_column("facturas_electronicas", "xml_contenido")
