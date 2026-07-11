"""Operación de máquina EN VIVO (cronómetro + rotación de operadores) — feature PIM.

Capa de captura en vivo que al FINALIZAR se materializa en el parte de horas diario existente
(`registros_horas_maquina` + `turnos_horas_maquina`), reusando el motor de facturación/cartera. La
sesión abierta es estado EFÍMERO; la verdad facturable sigue siendo el parte diario.

Piezas:
  - CREATE enum `estado_sesion_maquina` (ABIERTA/FINALIZADA/ANULADA).
  - CREATE `sesiones_maquina`: una sesión de operación por máquina·obra·día. `iniciada_en`/`finalizada_en`
    TIMESTAMPTZ (el reloj real, instante absoluto). `asignacion_id` fija el precio/mínimo pactados.
    `registro_horas_id` FK `registros_horas_maquina` NULL: se setea al materializar (provenance + ancla
    anti-doble-facturación: finalizar dos veces es replay). Índice único PARCIAL `(maquina_id) WHERE
    estado='ABIERTA'` → una sola sesión abierta por máquina a la vez (invariante a nivel de base).
  - CREATE `tramos_operador`: franjas de operador EN VIVO, hijas de la sesión (ON DELETE CASCADE —
    anular/borrar la sesión arrastra sus tramos). `iniciado_en`/`finalizado_en` TIMESTAMPTZ
    (`finalizado_en` NULL = tramo corriendo). `horas_confirmadas` NUMERIC(18,4) NULL (lo confirmado por
    el humano al finalizar; el reloj propone, el supervisor ajusta). Índice único PARCIAL `(sesion_id)
    WHERE finalizado_en IS NULL` → un solo tramo abierto por sesión. Índice por `sesion_id`.

Solo CREATE (backward-compatible): las FKs apuntan a tablas que ya existen —`maquinas`/`trabajadores`
(0043), `obras` (0044), `asignaciones_maquina_obra`/`registros_horas_maquina` (0045)—; no altera
ninguna. Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0055_operacion_maquina_vivo
Revises: 0054_turnos_horas_maquina
Create Date: 2026-07-11
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0055_operacion_maquina_vivo"
down_revision: str | None = "0054_turnos_horas_maquina"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ESTADO_SESION = ("ABIERTA", "FINALIZADA", "ANULADA")
_CANTIDAD = sa.Numeric(18, 4)   # horas (misma precisión que la spec del vertical)


def upgrade() -> None:
    etiquetas = ", ".join(f"'{v}'" for v in _ESTADO_SESION)
    op.execute(f"CREATE TYPE estado_sesion_maquina AS ENUM ({etiquetas})")

    op.create_table(
        "sesiones_maquina",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("maquina_id", sa.BigInteger, sa.ForeignKey("maquinas.id"), nullable=False),
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id"), nullable=False),
        sa.Column(
            "asignacion_id",
            sa.BigInteger,
            sa.ForeignKey("asignaciones_maquina_obra.id"),
            nullable=False,
        ),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADO_SESION, name="estado_sesion_maquina", create_type=False),
            nullable=False,
            server_default="ABIERTA",
        ),
        sa.Column("iniciada_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finalizada_en", sa.TIMESTAMP(timezone=True)),
        # provenance del parte materializado + ancla anti-doble-facturación (NULL hasta finalizar).
        sa.Column("registro_horas_id", sa.BigInteger, sa.ForeignKey("registros_horas_maquina.id")),
        sa.Column("notas", sa.Text),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # Una sola sesión ABIERTA por máquina a la vez (las FINALIZADA/ANULADA no cuentan).
    op.execute(
        "CREATE UNIQUE INDEX uq_sesion_maquina_abierta "
        "ON sesiones_maquina (maquina_id) WHERE estado = 'ABIERTA'"
    )

    op.create_table(
        "tramos_operador",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "sesion_id",
            sa.BigInteger,
            sa.ForeignKey("sesiones_maquina.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operador_id", sa.BigInteger, sa.ForeignKey("trabajadores.id")),
        sa.Column("iniciado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finalizado_en", sa.TIMESTAMP(timezone=True)),
        sa.Column("horas_confirmadas", _CANTIDAD),
        sa.Column("creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tramos_operador_sesion", "tramos_operador", ["sesion_id"])
    # Un solo tramo abierto (finalizado_en NULL) por sesión: la rotación cierra el anterior antes de abrir.
    op.execute(
        "CREATE UNIQUE INDEX uq_tramo_operador_abierto "
        "ON tramos_operador (sesion_id) WHERE finalizado_en IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_tramo_operador_abierto")
    op.drop_index("ix_tramos_operador_sesion", table_name="tramos_operador")
    op.drop_table("tramos_operador")
    op.execute("DROP INDEX IF EXISTS uq_sesion_maquina_abierta")
    op.drop_table("sesiones_maquina")
    op.execute("DROP TYPE estado_sesion_maquina")
