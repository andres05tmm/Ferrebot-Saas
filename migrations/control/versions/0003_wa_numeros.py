"""wa_numeros: mapeo del número/canal de WhatsApp (Kapso) → empresa.

El webhook único de Kapso atiende a todos los tenants; cada payload trae el `phone_number_id` del
número que recibió el mensaje. Esta tabla resuelve a qué empresa pertenece (DB-per-tenant: de ahí
sale la base). No guarda secretos: las credenciales de Kapso son de plataforma (env). `numero` y
`waba_id` son datos de referencia.

Seed del número de prueba (NO va en la migración para no acoplar datos al esquema; el
`phone_number_id` real no es secreto pero sí específico del entorno). Hacerlo a mano una vez:

    INSERT INTO wa_numeros (phone_number_id, empresa_id, numero, estado)
    VALUES ('<phone_number_id_de_kapso>', <empresa_id>, '+57…', 'activo');

o con `python -m tools.seed_wa_numero <phone_number_id> <slug> [--numero +57…]`.

Revision ID: 0003_wa_numeros
Revises: 0002_config_empresa
Create Date: 2026-06-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_wa_numeros"
down_revision: str | None = "0002_config_empresa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wa_numeros",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("phone_number_id", sa.Text, nullable=False),
        sa.Column("empresa_id", sa.BigInteger, sa.ForeignKey("empresas.id"), nullable=False),
        sa.Column("waba_id", sa.Text),
        sa.Column("numero", sa.Text),                       # número legible (+57…), referencia
        sa.Column("estado", sa.Text, nullable=False, server_default="activo"),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        # Un phone_number_id mapea a exactamente una empresa.
        sa.UniqueConstraint("phone_number_id", name="uq_wa_numeros_phone_number_id"),
    )
    op.create_index("ix_wa_numeros_empresa", "wa_numeros", ["empresa_id"])


def downgrade() -> None:
    op.drop_table("wa_numeros")
