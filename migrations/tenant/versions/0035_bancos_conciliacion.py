"""Conciliación bancaria (ADR 0028): extiende `bancolombia_transferencias` con estado + enlace.

La tabla existía desde la 0001 como bitácora de transferencias ENTRANTES parseadas de Gmail
(`gmail_message_id` UNIQUE = idempotencia de ESE canal). La conciliación la ADOPTA como el libro de
movimientos bancarios y la extiende, de forma ADITIVA:

- `gmail_message_id` pasa a NULLABLE: la ingesta de un extracto no viene de Gmail. La UNIQUE se
  conserva (Postgres admite múltiples NULL), así el canal Gmail sigue idempotente por su id.
- `referencia_bancaria` (TEXT) + índice UNIQUE parcial: ANCLA de idempotencia de la ingesta del
  extracto — reprocesar el mismo extracto NO duplica movimientos.
- `naturaleza` (TEXT + CHECK 'credito'/'debito'): un extracto trae créditos (ventas por transferencia)
  y débitos (gastos/abonos pagados por banco). Las filas viejas de Gmail son entrantes → 'credito'.
- `estado_conciliacion` (enum `conciliacion_estado`): no_conciliado → sugerido → conciliado.
- `conciliado_con_tipo`/`conciliado_con_id`: enlace polimórfico (FK-less, como ventas→usuarios) al
  movimiento interno (venta/gasto/abono). Conciliar SOLO escribe estas columnas: no toca saldos.
- `conciliado_en`: sello de la confirmación explícita.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0035_bancos_conciliacion
Revises: 0030_orm_huerfanas
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_bancos_conciliacion"
down_revision: str | None = "0030_orm_huerfanas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE TYPE conciliacion_estado AS ENUM ('no_conciliado', 'sugerido', 'conciliado')")

    op.alter_column("bancolombia_transferencias", "gmail_message_id", nullable=True)

    op.add_column(
        "bancolombia_transferencias",
        sa.Column("referencia_bancaria", sa.Text, nullable=True),
    )
    # Idempotencia de la ingesta del extracto: la misma referencia no se inserta dos veces. Parcial
    # (WHERE NOT NULL) para no chocar con las filas históricas de Gmail (referencia_bancaria NULL).
    op.execute(
        "CREATE UNIQUE INDEX uq_banco_referencia_bancaria "
        "ON bancolombia_transferencias (referencia_bancaria) "
        "WHERE referencia_bancaria IS NOT NULL"
    )

    op.add_column(
        "bancolombia_transferencias",
        sa.Column(
            "naturaleza", sa.Text, nullable=False, server_default="credito",
        ),
    )
    op.create_check_constraint(
        "ck_banco_naturaleza",
        "bancolombia_transferencias",
        "naturaleza IN ('credito', 'debito')",
    )

    op.add_column(
        "bancolombia_transferencias",
        sa.Column(
            "estado_conciliacion",
            sa.Enum("no_conciliado", "sugerido", "conciliado",
                    name="conciliacion_estado", create_type=False),
            nullable=False,
            server_default="no_conciliado",
        ),
    )
    op.add_column(
        "bancolombia_transferencias",
        sa.Column("conciliado_con_tipo", sa.Text, nullable=True),
    )
    op.add_column(
        "bancolombia_transferencias",
        sa.Column("conciliado_con_id", sa.BigInteger, nullable=True),
    )
    op.add_column(
        "bancolombia_transferencias",
        sa.Column("conciliado_en", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bancolombia_transferencias", "conciliado_en")
    op.drop_column("bancolombia_transferencias", "conciliado_con_id")
    op.drop_column("bancolombia_transferencias", "conciliado_con_tipo")
    op.drop_column("bancolombia_transferencias", "estado_conciliacion")
    op.execute("DROP TYPE conciliacion_estado")
    op.drop_constraint("ck_banco_naturaleza", "bancolombia_transferencias", type_="check")
    op.drop_column("bancolombia_transferencias", "naturaleza")
    op.execute("DROP INDEX IF EXISTS uq_banco_referencia_bancaria")
    op.drop_column("bancolombia_transferencias", "referencia_bancaria")
    # Restaura el NOT NULL original (en base efímera/vacía es limpio; en prod no se espera downgrade).
    op.alter_column("bancolombia_transferencias", "gmail_message_id", nullable=False)
