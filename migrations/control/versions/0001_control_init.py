"""control init — plano de control (schema.md)

Revision ID: 0001_control
Revises:
Create Date: 2026-06-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_control"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS = {
    "tenant_estado": ("provisionando", "activa", "suspendida", "vencida"),
    "suscripcion_estado": ("prueba", "activa", "suspendida", "vencida"),
    "global_rol": ("super_admin",),
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    for name, values in _ENUMS.items():
        valores = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({valores})")

    op.create_table(
        "planes",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("limites", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("precio_mensual", sa.Numeric(12, 2)),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "empresas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("nit", sa.Text, nullable=False, unique=True),
        sa.Column("slug", sa.Text, nullable=False, unique=True),
        sa.Column("estado", _enum("tenant_estado"), nullable=False, server_default="provisionando"),
        sa.Column("plan_id", sa.BigInteger, sa.ForeignKey("planes.id")),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "tenant_databases",
        sa.Column("empresa_id", sa.BigInteger, sa.ForeignKey("empresas.id"), primary_key=True),
        sa.Column("db_name", sa.Text, nullable=False),
        sa.Column("host", sa.Text, nullable=False),
        sa.Column("connection_url_cifrada", sa.LargeBinary, nullable=False),
        sa.Column("region", sa.Text),
    )

    op.create_table(
        "suscripciones",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("empresa_id", sa.BigInteger, sa.ForeignKey("empresas.id"), nullable=False),
        sa.Column("plan_id", sa.BigInteger, sa.ForeignKey("planes.id")),
        sa.Column("estado", _enum("suscripcion_estado"), nullable=False, server_default="activa"),
        sa.Column("periodo_inicio", sa.Date),
        sa.Column("periodo_fin", sa.Date),
    )

    op.create_table(
        "secretos_empresa",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("empresa_id", sa.BigInteger, sa.ForeignKey("empresas.id"), nullable=False),
        sa.Column("clave", sa.Text, nullable=False),
        sa.Column("valor_cifrado", sa.LargeBinary, nullable=False),
        sa.Column("nonce", sa.LargeBinary, nullable=False),
        sa.Column("actualizado_en", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("empresa_id", "clave", name="uq_secretos_empresa_clave"),
    )

    op.create_table(
        "branding",
        sa.Column("empresa_id", sa.BigInteger, sa.ForeignKey("empresas.id"), primary_key=True),
        sa.Column("logo_url", sa.Text),
        sa.Column("color_primario", sa.Text, server_default="#C8200E"),
        sa.Column("nombre_comercial", sa.Text),
        sa.Column("dominio", sa.Text),
    )

    op.create_table(
        "empresa_features",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("empresa_id", sa.BigInteger, sa.ForeignKey("empresas.id"), nullable=False),
        sa.Column("feature", sa.Text, nullable=False),
        sa.Column("habilitada", sa.Boolean, nullable=False),
        sa.UniqueConstraint("empresa_id", "feature", name="uq_empresa_features"),
    )

    op.create_table(
        "super_admins",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("nombre", sa.Text),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    for table in (
        "super_admins", "empresa_features", "branding", "secretos_empresa",
        "suscripciones", "tenant_databases", "empresas", "planes",
    ):
        op.drop_table(table)
    for name in _ENUMS:
        op.execute(f"DROP TYPE {name}")
