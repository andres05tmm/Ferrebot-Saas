"""Pack postventa (plan §2.6): seguimiento tras el evento — encuesta 1-5, reseña, recompra.

90% worker + plantillas (el job barre citas cumplidas / pedidos entregados y envía la plantilla de
seguimiento) + una herramienta (`calificar_atencion`). Tablas:

- `postventa_config` (una fila): activo, horas tras el evento, qué disparadores, link de reseña
  (Google Maps del negocio) y umbral de calificación para pedirla.
- `postventa_envios`: log/dedup append-only — un seguimiento por (origen, origen_id), jamás se repite.
- `encuestas_respuestas`: la calificación 1-5 (+comentario) que registra el agente.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0023_postventa
Revises: 0022_reservas
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_postventa"
down_revision: str | None = "0022_reservas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "postventa_config",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("horas_tras_evento", sa.Integer, nullable=False, server_default="3"),
        sa.Column("seguir_citas", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("seguir_pedidos", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("google_maps_url", sa.Text),
        sa.Column("calificacion_minima_resena", sa.Integer, nullable=False, server_default="4"),
    )

    op.create_table(
        "postventa_envios",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("origen", sa.Text, nullable=False),          # cita | pedido
        sa.Column("origen_id", sa.BigInteger, nullable=False),
        sa.Column("telefono", sa.Text, nullable=False),
        sa.Column(
            "enviado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("origen", "origen_id", name="uq_postventa_envios_origen"),
    )

    op.create_table(
        "encuestas_respuestas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("telefono", sa.Text, nullable=False),
        sa.Column("calificacion", sa.Integer, nullable=False),
        sa.Column("comentario", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.CheckConstraint("calificacion BETWEEN 1 AND 5", name="ck_encuesta_calificacion"),
    )
    op.create_index("ix_encuestas_creado", "encuestas_respuestas", ["creado_en"])


def downgrade() -> None:
    op.drop_index("ix_encuestas_creado", table_name="encuestas_respuestas")
    op.drop_table("encuestas_respuestas")
    op.drop_table("postventa_envios")
    op.drop_table("postventa_config")
