"""dispositivos_impresion: token de dispositivo por sede para el agente de impresión (ADR 0033 D6).

Vive en el CONTROL DB porque autentica ANTES de abrir la base del tenant (mismo plano que las
credenciales por empresa). Se guarda el HASH (sha256) del token — el servidor jamás necesita el
texto plano, que se muestra UNA vez al emitirlo. Revocable (`activo=false`, `revocado_en`).
El token solo autoriza la superficie `/api/v1/impresion` (lo impone la dependencia del router).

Revision ID: 0011_dispositivos_impresion
Revises: 0010_gmail_cuentas
Create Date: 2026-07-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_dispositivos_impresion"
down_revision: str | None = "0010_gmail_cuentas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dispositivos_impresion",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "empresa_id", sa.BigInteger,
            sa.ForeignKey("empresas.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("nombre", sa.Text, nullable=False),   # "Caja principal", "Cocina sede norte"
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revocado_en", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_dispositivos_impresion_empresa", "dispositivos_impresion", ["empresa_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_dispositivos_impresion_empresa", table_name="dispositivos_impresion")
    op.drop_table("dispositivos_impresion")
