"""Tab Gastos: qué TIPO de egreso es cada salida + los gastos recurrentes del mes.

Dos cosas y una taxonomía:

1. **`gastos.tipo_egreso`** — no todo lo que sale de la caja es gasto. Un retiro del dueño reparte
   utilidad y la compra de una vitrina compra un activo: los dos mueven plata pero NINGUNO resta en
   la utilidad del mes. Sin esta columna solo quedan dos salidas malas: no registrarlos (la caja no
   cuadra) o registrarlos como gasto (la utilidad miente). Default `gasto` — todo lo histórico lo era.
2. **`gastos_recurrentes`** — lo que se paga todos los meses (arriendo, luz, nómina). El gasto que lo
   paga guarda `recurrente_id`: el checklist del mes se sabe por el vínculo, sin adivinar por nombre.

Además `gasto_categoria` += arriendo · empaque · impuestos · comisiones · aseo (ADD VALUE en
autocommit, patrón 0016/0022): la taxonomía de 6 del POS le quedaba corta a una ferretería —
el arriendo y las bolsas, que son de los egresos más grandes, caían en `otros`.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0071_gastos_tipo_y_recurrentes
Revises: 0070_factura_proveedor_id
Create Date: 2026-07-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0071_gastos_tipo_y_recurrentes"
down_revision: str | None = "0070_factura_proveedor_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATEGORIAS_NUEVAS = ("arriendo", "empaque", "impuestos", "comisiones", "aseo")


def upgrade() -> None:
    # ADD VALUE no corre dentro de una transacción → autocommit (patrón 0016/0022). IF NOT EXISTS lo
    # hace idempotente entre reintentos del runner multi-empresa.
    with op.get_context().autocommit_block():
        for valor in CATEGORIAS_NUEVAS:
            op.execute(f"ALTER TYPE gasto_categoria ADD VALUE IF NOT EXISTS '{valor}'")

    tipo_egreso = postgresql.ENUM(
        "gasto", "inversion", "retiro", "pago_deuda", name="tipo_egreso"
    )
    tipo_egreso.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "gastos",
        sa.Column(
            "tipo_egreso",
            postgresql.ENUM(name="tipo_egreso", create_type=False),
            nullable=False,
            server_default="gasto",
        ),
    )

    op.create_table(
        "gastos_recurrentes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column(
            "categoria",
            postgresql.ENUM(name="gasto_categoria", create_type=False),
            nullable=False,
        ),
        # Cuánto suele costar (referencia para el checklist); el monto real lo pone el gasto.
        sa.Column("monto_estimado", sa.Numeric(12, 2)),
        sa.Column("dia_mes", sa.Integer),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("dia_mes IS NULL OR (dia_mes BETWEEN 1 AND 31)", name="ck_recurrente_dia"),
    )
    op.add_column(
        "gastos",
        sa.Column(
            "recurrente_id",
            sa.BigInteger,
            sa.ForeignKey("gastos_recurrentes.id", ondelete="SET NULL"),
        ),
    )
    # El checklist pregunta "¿qué recurrentes ya se pagaron este mes?": índice por vínculo + fecha.
    op.create_index(
        "ix_gastos_recurrente_creado", "gastos", ["recurrente_id", "creado_en"]
    )


def downgrade() -> None:
    op.drop_index("ix_gastos_recurrente_creado", table_name="gastos")
    op.drop_column("gastos", "recurrente_id")
    op.drop_table("gastos_recurrentes")
    op.drop_column("gastos", "tipo_egreso")
    postgresql.ENUM(name="tipo_egreso").drop(op.get_bind(), checkfirst=True)
    # Quitar valores de un ENUM en PG exige recrear el tipo; los de `gasto_categoria` quedan
    # (inofensivos: nadie los usa si no hay filas con ellos).
