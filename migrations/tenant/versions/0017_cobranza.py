"""Pack cobranza (ADR 0015): recordatorios de cartera por WhatsApp sobre los fiados existentes.

No toca `fiados`/`fiados_movimientos` (la fuente de verdad del saldo sigue intacta); agrega el plano
de cobranza:

- `cobranza_config` (una fila): reglas del negocio — cadencia, tope, ventana horaria, saldo mínimo.
- `cobranza_clientes` (estado por cliente): opt-out (Habeas Data) + dedup/tope de recordatorios.
- `promesas_pago`: promesa de pago registrada por el agente (`vigente → cumplida | incumplida | reemplazada`).
- `pagos_reportados`: "ya pagué" del cliente → bandeja por verificar del dashboard.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0017_cobranza
Revises: 0016_fe_estado_anulada
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_cobranza"
down_revision: str | None = "0016_fe_estado_anulada"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROMESA = ("vigente", "cumplida", "incumplida", "reemplazada")


def upgrade() -> None:
    valores = ", ".join(f"'{v}'" for v in _PROMESA)
    op.execute(f"CREATE TYPE promesa_estado AS ENUM ({valores})")

    op.create_table(
        "cobranza_config",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("cadencia_dias", sa.Integer, nullable=False, server_default="7"),
        sa.Column("max_recordatorios", sa.Integer, nullable=False, server_default="3"),
        sa.Column("hora_inicio", sa.Time, nullable=False, server_default="09:00"),
        sa.Column("hora_fin", sa.Time, nullable=False, server_default="19:00"),
        sa.Column("saldo_minimo", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )

    op.create_table(
        "cobranza_clientes",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_id", sa.BigInteger, nullable=False, unique=True),
        sa.Column("opt_out", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("recordatorios_enviados", sa.Integer, nullable=False, server_default="0"),
        sa.Column("ultimo_recordatorio_en", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "promesas_pago",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_id", sa.BigInteger, nullable=False),
        sa.Column("telefono", sa.Text, nullable=False),
        sa.Column("fecha_promesa", sa.Date, nullable=False),
        sa.Column(
            "estado",
            postgresql.ENUM(*_PROMESA, name="promesa_estado", create_type=False),
            nullable=False, server_default="vigente",
        ),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    # Escaneo del motor: la promesa vigente de un cliente.
    op.create_index("ix_promesas_cliente_estado", "promesas_pago", ["cliente_id", "estado"])

    op.create_table(
        "pagos_reportados",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_id", sa.BigInteger, nullable=False),
        sa.Column("telefono", sa.Text, nullable=False),
        sa.Column("nota", sa.Text),
        sa.Column("verificado", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    # Bandeja del dashboard: los no verificados primero.
    op.create_index("ix_pagos_reportados_verificado", "pagos_reportados", ["verificado", "creado_en"])


def downgrade() -> None:
    op.drop_index("ix_pagos_reportados_verificado", table_name="pagos_reportados")
    op.drop_table("pagos_reportados")
    op.drop_index("ix_promesas_cliente_estado", table_name="promesas_pago")
    op.drop_table("promesas_pago")
    op.drop_table("cobranza_clientes")
    op.drop_table("cobranza_config")
    op.execute("DROP TYPE promesa_estado")
