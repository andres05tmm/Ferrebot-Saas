"""Hilo de mensajes del handoff — inbox bidireccional (docs/plan-dashboard-agente-2026.md Fase 2).

La tabla `conversaciones` (0009) lleva el ESTADO (`bot`|`humano`) de cada cliente, pero NO el hilo de
mensajes. Para renderizar y responder desde el dashboard hace falta persistir cada mensaje (entrante
del cliente, respuesta del bot y respuesta del asesor) en una tabla nueva del árbol TENANT.

`conversacion_mensajes`: una fila por mensaje. `cliente_telefono` es una FK LÓGICA a
`conversaciones.cliente_telefono` (no se fuerza con constraint: el primer mensaje de un cliente llega
antes de que exista su fila de estado, y el runtime la asegura aparte). `direccion`
(`entrante`|`saliente`) y `autor` (`cliente`|`bot`|`asesor`) son enums. Índice por
(`cliente_telefono`, `creada_en`) para traer el hilo ordenado de un cliente.

Aislada por construcción (base del propio tenant, sin `empresa_id`). Fechas en TIMESTAMPTZ (se operan
en hora Colombia, regla no negociable #4).

Downgrade: dropea la tabla y luego los dos tipos enum.

Revision ID: 0024_conversacion_mensajes
Revises: 0023_postventa
Create Date: 2026-06-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_conversacion_mensajes"
down_revision: str | None = "0023_postventa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIRECCIONES = ("entrante", "saliente")
_AUTORES = ("cliente", "bot", "asesor")


def upgrade() -> None:
    op.execute(f"CREATE TYPE mensaje_direccion AS ENUM ({', '.join(repr(v) for v in _DIRECCIONES)})")
    op.execute(f"CREATE TYPE mensaje_autor AS ENUM ({', '.join(repr(v) for v in _AUTORES)})")

    op.create_table(
        "conversacion_mensajes",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # FK LÓGICA a conversaciones.cliente_telefono (sin constraint: el 1er mensaje precede a la fila
        # de estado). El número de WhatsApp = identidad del cliente.
        sa.Column("cliente_telefono", sa.Text, nullable=False),
        sa.Column(
            "direccion",
            postgresql.ENUM(*_DIRECCIONES, name="mensaje_direccion", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "autor",
            postgresql.ENUM(*_AUTORES, name="mensaje_autor", create_type=False),
            nullable=False,
        ),
        sa.Column("texto", sa.Text, nullable=False),
        sa.Column(
            "creada_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
    )
    # Hilo de un cliente, en orden cronológico (y último mensaje por cliente para el listado del inbox).
    op.create_index(
        "ix_conversacion_mensajes_telefono_creada",
        "conversacion_mensajes",
        ["cliente_telefono", "creada_en"],
    )


def downgrade() -> None:
    op.drop_table("conversacion_mensajes")
    op.execute("DROP TYPE mensaje_autor")
    op.execute("DROP TYPE mensaje_direccion")
