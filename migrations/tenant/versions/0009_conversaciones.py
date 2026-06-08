"""Estado de conversación / handoff — capacidad TRANSVERSAL (docs/whatsapp-agentes-arquitectura.md §handoff).

Crea la tabla `conversaciones` en el árbol TENANT (son datos de negocio, no control): el estado de la
conversación de cada cliente de cara al público (`bot` | `humano`). Cuando un agente escala a un humano,
la fila pasa a `humano`; el runtime de WhatsApp NO corre el agente mientras esté en `humano` (lo
reanuda el negocio desde el dashboard). No es exclusivo de agenda: cualquier agente de cara al cliente
la usa.

Una fila por `cliente_telefono` (su número de WhatsApp = identidad, único). `estado` es enum. Aislada
por construcción (base del propio tenant, sin `empresa_id`). `escalada_en`/`resuelta_en` registran el
ciclo del handoff actual; `creada_en` es el alta de la fila.

Downgrade: dropea la tabla y luego el tipo enum.

Revision ID: 0009_conversaciones
Revises: 0008_agenda_citas
Create Date: 2026-06-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_conversaciones"
down_revision: str | None = "0008_agenda_citas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ESTADOS = ("bot", "humano")


def upgrade() -> None:
    valores = ", ".join(f"'{v}'" for v in _ESTADOS)
    op.execute(f"CREATE TYPE conversacion_estado AS ENUM ({valores})")

    op.create_table(
        "conversaciones",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # El teléfono = identidad del cliente (su número de WhatsApp). Una conversación por cliente.
        sa.Column("cliente_telefono", sa.Text, nullable=False, unique=True),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADOS, name="conversacion_estado", create_type=False),
            nullable=False,
            server_default="bot",
        ),
        sa.Column("motivo", sa.Text),                                    # por qué se escaló (del agente)
        sa.Column("creada_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("escalada_en", sa.TIMESTAMP(timezone=True)),           # inicio del handoff actual
        sa.Column("resuelta_en", sa.TIMESTAMP(timezone=True)),           # null mientras siga en humano
    )
    # Listado de escaladas (estado=humano) en el dashboard.
    op.create_index("ix_conversaciones_estado", "conversaciones", ["estado"])


def downgrade() -> None:
    op.drop_table("conversaciones")
    op.execute("DROP TYPE conversacion_estado")
