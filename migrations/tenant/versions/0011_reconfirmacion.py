"""Reconfirmación de citas (anti-no-show) — sub-estado + dedup del recordatorio + corte de riesgo.

Base de la capacidad "recordatorio + reconfirmación" del pack Agenda. NO toca `citas.estado` (el ciclo
de vida sigue igual: solo la cancelación explícita libera el cupo); agrega un SUB-estado paralelo:

- `citas.confirmacion` (enum `esperando` | `reconfirmada` | `en_riesgo`, default `esperando`): seguimiento
  de la reconfirmación del cliente. `en_riesgo` = pasó el corte sin respuesta (NUNCA libera el cupo).
- `citas.recordatorio_enviado_en` (timestamptz, nullable): cuándo se envió el recordatorio de
  reconfirmación; sirve de dedup (no reenviar).
- `agenda_config.corte_riesgo_horas` (int, default 2): horas antes de la cita para marcar `en_riesgo`
  si no hubo respuesta. Los tiempos del recordatorio reusan `recordatorios_horas` (ya existe).

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0011_reconfirmacion
Revises: 0010_gcal_sync
Create Date: 2026-06-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_reconfirmacion"
down_revision: str | None = "0010_gcal_sync"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONFIRMACION = ("esperando", "reconfirmada", "en_riesgo")


def upgrade() -> None:
    valores = ", ".join(f"'{v}'" for v in _CONFIRMACION)
    op.execute(f"CREATE TYPE cita_confirmacion AS ENUM ({valores})")

    op.add_column(
        "citas",
        sa.Column(
            "confirmacion",
            postgresql.ENUM(*_CONFIRMACION, name="cita_confirmacion", create_type=False),
            nullable=False,
            server_default="esperando",
        ),
    )
    op.add_column("citas", sa.Column("recordatorio_enviado_en", sa.TIMESTAMP(timezone=True)))
    # Escaneo del job: citas por (confirmacion, inicio) en una ventana.
    op.create_index("ix_citas_confirmacion_inicio", "citas", ["confirmacion", "inicio"])

    op.add_column(
        "agenda_config",
        sa.Column("corte_riesgo_horas", sa.Integer, nullable=False, server_default="2"),
    )


def downgrade() -> None:
    op.drop_column("agenda_config", "corte_riesgo_horas")
    op.drop_index("ix_citas_confirmacion_inicio", table_name="citas")
    op.drop_column("citas", "recordatorio_enviado_en")
    op.drop_column("citas", "confirmacion")
    op.execute("DROP TYPE cita_confirmacion")
