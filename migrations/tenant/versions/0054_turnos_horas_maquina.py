"""Rotación de operadores: turnos de un parte de horas de máquina (feature PIM).

Una máquina puede rotar operadores el mismo día (Juan 8:00-13:00, Pedro 14:00-17:00). El PARTE por
máquina·obra·día sigue siendo el agregado (un `RegistroHorasMaquina` por día); cada franja de operador
se guarda como un `TurnoHorasMaquina` hijo. Las horas del día = Σ turnos; el mínimo facturable se aplica
UNA vez al total del día (la rotación NUNCA multiplica el cobro). El pago a trabajadores va por otro eje.

Piezas:
  - CREATE `turnos_horas_maquina`: hijos de `registros_horas_maquina` (ON DELETE CASCADE — borrar el parte
    arrastra sus turnos). `operador_id` FK trabajadores NULL; franja `hora_inicio`/`hora_fin` (TIME) NULL
    (informativa); `horas` NUMERIC(18,4) NOT NULL (la unidad de negocio, no se deriva de la franja).
    Índice por `registro_horas_id` (se listan por parte).
  - ALTER `cargos_alquiler` ADD `turno_id` (BigInteger NULL, FK turnos_horas_maquina): el cargo DELTA de un
    turno que sube las facturables del día se ancla al turno; el primer asiento del parte conserva
    `turno_id` NULL (cargo a nivel de registro, como hoy).
  - Se REEMPLAZA el `UNIQUE(registro_horas_id)` de 0049 por DOS índices únicos PARCIALES: uno sobre
    `(registro_horas_id) WHERE turno_id IS NULL` (un solo cargo de registro por parte) y otro sobre
    `(turno_id) WHERE turno_id IS NOT NULL` (un solo cargo delta por turno). Ambos anclan la idempotencia
    del carve-out «un registro/turno no genera dos cargos» a nivel de base.

Backward-compatible: tabla nueva vacía + columna nullable + reemplazo de constraint por índices equivalentes
para las filas existentes (todas con `turno_id` NULL, cubiertas por el primer índice parcial). Se aplica a
todas las empresas vía `tools.migrate_tenants`. NO crea enums nuevos.

Revision ID: 0054_turnos_horas_maquina
Revises: 0053_ventas_pagos
Create Date: 2026-07-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_turnos_horas_maquina"
down_revision: str | None = "0053_ventas_pagos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CANTIDAD = sa.Numeric(18, 4)   # horas del vertical construcción (spec: Decimal 18,4)

# Nombre auto-generado por Postgres para el UNIQUE inline de 0049 (`unique=True` en la columna).
_UNIQUE_0049 = "cargos_alquiler_registro_horas_id_key"


def upgrade() -> None:
    op.create_table(
        "turnos_horas_maquina",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "registro_horas_id",
            sa.BigInteger,
            sa.ForeignKey("registros_horas_maquina.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("operador_id", sa.BigInteger, sa.ForeignKey("trabajadores.id")),
        sa.Column("hora_inicio", sa.Time),
        sa.Column("hora_fin", sa.Time),
        sa.Column("horas", _CANTIDAD, nullable=False),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_turnos_horas_maquina_registro_horas_id",
        "turnos_horas_maquina",
        ["registro_horas_id"],
    )

    # cargos_alquiler gana `turno_id` (NULL = cargo del registro; NOT NULL = cargo delta de un turno).
    op.add_column(
        "cargos_alquiler",
        sa.Column(
            "turno_id",
            sa.BigInteger,
            sa.ForeignKey("turnos_horas_maquina.id"),
        ),
    )
    # Reemplaza el UNIQUE(registro_horas_id) por dos únicos PARCIALES (un cargo de registro por parte y un
    # cargo delta por turno). El DROP es IF EXISTS por si el nombre auto-generado difiere en alguna base.
    op.execute(f"ALTER TABLE cargos_alquiler DROP CONSTRAINT IF EXISTS {_UNIQUE_0049}")
    op.execute(
        "CREATE UNIQUE INDEX uq_cargos_alquiler_registro_sin_turno "
        "ON cargos_alquiler (registro_horas_id) WHERE turno_id IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_cargos_alquiler_turno "
        "ON cargos_alquiler (turno_id) WHERE turno_id IS NOT NULL"
    )


def downgrade() -> None:
    # Restaura el UNIQUE(registro_horas_id) de 0049 y retira el plano de turnos. Requiere que no queden
    # cargos de turno (turno_id NOT NULL); en un rollback limpio la tabla nueva se elimina primero.
    op.execute("DROP INDEX IF EXISTS uq_cargos_alquiler_turno")
    op.execute("DROP INDEX IF EXISTS uq_cargos_alquiler_registro_sin_turno")
    op.drop_column("cargos_alquiler", "turno_id")
    op.create_unique_constraint(_UNIQUE_0049, "cargos_alquiler", ["registro_horas_id"])

    op.drop_index("ix_turnos_horas_maquina_registro_horas_id", table_name="turnos_horas_maquina")
    op.drop_table("turnos_horas_maquina")
