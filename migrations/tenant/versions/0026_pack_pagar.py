"""Pack pagar (ADR 0019): avisos internos al DUEÑO de cuentas por pagar próximas a vencer/vencidas.

Espejo de `pack_cobranza` al otro lado de la cartera, pero es un AVISO INTERNO (no un agente de cara a
un tercero): por eso NO lleva opt-out, ni promesas, ni herramientas de cliente. El motor solo LEE
`facturas_proveedores` (la fuente de verdad del saldo sigue intacta: `pendiente` lo recalcula el flujo
de abonos existente). Esta migración agrega el plano de avisos:

- `facturas_proveedores.fecha_vencimiento` (DATE, nullable): vencimiento de la cuenta. NULL = el motor
  lo deriva de `fecha + plazo_default_dias`. Segura sobre datos existentes (queda NULL).
- `pagar_config` (una fila): reglas del aviso — ventana horaria, días de aviso previo, cadencia y plazo.
- `pagar_avisos` (estado por factura): dedup/cadencia — cuántas veces se avisó de ESA factura y cuándo.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0026_pack_pagar
Revises: 0025_compras_idem
Create Date: 2026-06-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_pack_pagar"
down_revision: str | None = "0025_compras_idem"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "facturas_proveedores",
        sa.Column("fecha_vencimiento", sa.Date, nullable=True),
    )

    op.create_table(
        "pagar_config",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        # Avisar N días ANTES de vencer (0 = solo al vencer / vencidas).
        sa.Column("dias_aviso_previo", sa.Integer, nullable=False, server_default="3"),
        # No repetir el aviso de la misma factura antes de N días.
        sa.Column("cadencia_dias", sa.Integer, nullable=False, server_default="3"),
        sa.Column("hora_inicio", sa.Time, nullable=False, server_default="08:00"),
        sa.Column("hora_fin", sa.Time, nullable=False, server_default="18:00"),
        # Vencimiento derivado cuando `fecha_vencimiento` es NULL.
        sa.Column("plazo_default_dias", sa.Integer, nullable=False, server_default="30"),
    )

    op.create_table(
        "pagar_avisos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "factura_id",
            sa.Text,
            sa.ForeignKey("facturas_proveedores.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("avisos_enviados", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ultimo_aviso_en", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("pagar_avisos")
    op.drop_table("pagar_config")
    op.drop_column("facturas_proveedores", "fecha_vencimiento")
