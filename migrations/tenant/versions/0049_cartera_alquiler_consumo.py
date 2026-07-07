"""Cartera de alquiler (Fase 5): cupos de crédito, traza idempotente de cargos y config de colitas +
cierre M2 (idempotencia del consumo de inventario).

Novena migración del vertical construcción (Ola B, sobre 0048). Backward-compatible: solo CREATE de
tablas nuevas (`cupos_alquiler`, `cargos_alquiler`, `cartera_config`) + un ADD COLUMN nullable sobre
`consumos_inventario` (tabla del vertical, dueña 0044). Se aplica vacía al resto de empresas vía
`tools.migrate_tenants` (tablas vacías / columna NULL no pesan; el acceso lo gatea la flag
`cartera_alquiler`). NO crea enums nuevos.

Piezas (diseño `docs/research/pim-fase5-cartera-diseno.md`):
  - CREATE `cupos_alquiler` (§1.1): tope de crédito de alquiler por cliente (MONEY4). Índice ÚNICO PARCIAL
    `uq_cupos_alquiler_cliente_activo` WHERE activo = «un solo cupo ACTIVO por cliente» (cambiar de cupo =
    desactivar el vigente y crear otro; el histórico queda por `vigente_desde/hasta`). El saldo consumido
    NO vive aquí: la fuente de verdad sigue siendo el ledger de fiados (`clientes.saldo_fiado`); el cupo
    solo aporta el TOPE (§1.2).
  - CREATE `cargos_alquiler` (§1.3): tabla puente `RegistroHorasMaquina` → `Fiado`. `UNIQUE(registro_horas_id)`
    es el ANCLA DURA (a nivel de base) del invariante «un registro de horas no genera dos cargos en cartera»
    —defensa en profundidad sobre el lock de cliente de `FiadosService.crear`. Traza obra/máquina/asignación/
    monto para la vista de cartera por obra y el abono FIFO (§3). `monto` ya cuantizado al ledger (MONEY 12,2).
    FKs sin `ondelete`: obras/máquinas/clientes usan soft delete, así que el trace financiero nunca se borra
    en cascada (mismo criterio que `liquidaciones_obra`).
  - CREATE `cartera_config` (§1.4): una fila get-or-create (patrón `cobranza_config`/`pagar_config`) para la
    detección de «colita» estancada: `dias_colita` (N días sin abono) y `cadencia_aviso_dias` (dedup del aviso).
  - ALTER `consumos_inventario` ADD COLUMN `idempotency_key` (TEXT NULL) + índice ÚNICO PARCIAL
    `uq_consumos_inventario_idempotency_key` WHERE `idempotency_key IS NOT NULL`. CIERRE M2: hace idempotente
    `ObrasService.registrar_consumo` cuando el bot escribe consumos (reintento con la misma key → replay, sin
    segundo consumo ni segundo `movimiento_inventario`). NULL en las filas actuales: el alta de dashboard
    (sin key) conserva su comportamiento —el único parcial solo aplica WHERE NOT NULL.

Nota — NO se añade el índice de hardening `uq_fiados_idem` que sugería el §2.2 del diseño: la migración
0003 YA creó `uq_fiados_idempotency_key` (único parcial sobre `fiados.idempotency_key` WHERE IS NOT NULL),
así que la key `alquiler:horas:{id}` ya queda protegida a nivel de fiados (la nota del §0 quedó
desactualizada). Un segundo índice sería redundante.

Revision ID: 0049_cartera_alquiler_consumo
Revises: 0048_gastos_compras_liquidacion
Create Date: 2026-07-07

Nota: el `revision` id (`0049_cartera_alquiler_consumo`, 29 chars) cabe en `alembic_version.version_num`
(VARCHAR(32)).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_cartera_alquiler_consumo"
down_revision: str | None = "0048_gastos_compras_liquidacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY = sa.Numeric(12, 2)    # dinero del ledger de fiados (POS): el `monto` del cargo ya cuantizado
_MONEY4 = sa.Numeric(18, 4)   # dinero del vertical construcción: el `cupo` de crédito


def upgrade() -> None:
    # --- cupos_alquiler: tope de crédito de alquiler por cliente (el consumo lo aporta el ledger) -------
    op.create_table(
        "cupos_alquiler",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_id", sa.BigInteger, sa.ForeignKey("clientes.id"), nullable=False),
        sa.Column("cupo", _MONEY4, nullable=False),
        sa.Column("vigente_desde", sa.Date, nullable=False),
        sa.Column("vigente_hasta", sa.Date),   # NULL = sin vencimiento
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("notas", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Un solo cupo ACTIVO por cliente (histórico por vigente_desde/hasta al desactivar y recrear). Único
    # PARCIAL: permite muchos cupos inactivos, uno solo activo.
    op.execute(
        "CREATE UNIQUE INDEX uq_cupos_alquiler_cliente_activo "
        "ON cupos_alquiler (cliente_id) WHERE activo"
    )
    op.create_index("ix_cupos_alquiler_cliente_id", "cupos_alquiler", ["cliente_id"])

    # --- cargos_alquiler: puente RegistroHorasMaquina → Fiado. UNIQUE(registro_horas_id) = ancla dura de
    # idempotencia («un registro de horas no genera dos cargos»). Traza obra/máquina/asignación/monto. ---
    op.create_table(
        "cargos_alquiler",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "registro_horas_id",
            sa.BigInteger,
            sa.ForeignKey("registros_horas_maquina.id"),
            nullable=False,
            unique=True,   # ANCLA del invariante: un cargo por registro de horas
        ),
        sa.Column("fiado_id", sa.BigInteger, sa.ForeignKey("fiados.id"), nullable=False),
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id"), nullable=False),
        sa.Column("maquina_id", sa.BigInteger, sa.ForeignKey("maquinas.id"), nullable=False),
        sa.Column(
            "asignacion_id",
            sa.BigInteger,
            sa.ForeignKey("asignaciones_maquina_obra.id"),
            nullable=False,
        ),
        sa.Column("monto", _MONEY, nullable=False),   # ya cuantizado al ledger (12,2)
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_cargos_alquiler_obra_id", "cargos_alquiler", ["obra_id"])
    op.create_index("ix_cargos_alquiler_fiado_id", "cargos_alquiler", ["fiado_id"])

    # --- cartera_config: una fila get-or-create (detección de colita) -----------------------------------
    op.create_table(
        "cartera_config",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("dias_colita", sa.Integer, nullable=False, server_default="15"),
        sa.Column("cadencia_aviso_dias", sa.Integer, nullable=False, server_default="7"),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # --- CIERRE M2: idempotencia del consumo de inventario (reintento del bot → replay) -----------------
    op.add_column("consumos_inventario", sa.Column("idempotency_key", sa.Text))
    op.execute(
        "CREATE UNIQUE INDEX uq_consumos_inventario_idempotency_key "
        "ON consumos_inventario (idempotency_key) WHERE idempotency_key IS NOT NULL"
    )


def downgrade() -> None:
    # Orden inverso. Los índices/constraints de las tablas nuevas caen con el DROP TABLE; el índice y la
    # columna de `consumos_inventario` (tabla que sobrevive) se retiran explícitamente.
    op.execute("DROP INDEX IF EXISTS uq_consumos_inventario_idempotency_key")
    op.drop_column("consumos_inventario", "idempotency_key")

    op.drop_table("cartera_config")
    op.drop_table("cargos_alquiler")

    op.execute("DROP INDEX IF EXISTS uq_cupos_alquiler_cliente_activo")
    op.drop_table("cupos_alquiler")
