"""metodo_pago += 'datafono' — pago con datáfono (terminal de tarjeta).

Agrega el valor 'datafono' al enum `metodo_pago`. Gotcha: `ALTER TYPE ... ADD VALUE` no corre dentro
de una transacción en algunos Postgres (y env.py envuelve cada migración en una). Se usa
`op.get_context().autocommit_block()` para salir de la transacción; `IF NOT EXISTS` lo hace idempotente.
Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Downgrade: Postgres no soporta quitar un valor de un enum, así que se recrea el tipo sin 'datafono'
(rename → create → alter de la columna que lo usa → drop). `ventas.metodo_pago` es la única columna
con este tipo (0001).

Revision ID: 0007_metodo_pago_datafono
Revises: 0006_producto_proveedor
Create Date: 2026-06-06
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0007_metodo_pago_datafono"
down_revision: str | None = "0006_producto_proveedor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Valores ORIGINALES del enum (0001), sin 'datafono' — para reconstruirlo en el downgrade.
_VALORES_PREVIOS = ("efectivo", "transferencia", "tarjeta", "nequi", "daviplata", "fiado")


def upgrade() -> None:
    # ADD VALUE fuera de transacción (autocommit). IF NOT EXISTS = idempotente entre reintentos.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE metodo_pago ADD VALUE IF NOT EXISTS 'datafono'")


def downgrade() -> None:
    valores = ", ".join(f"'{v}'" for v in _VALORES_PREVIOS)
    op.execute("ALTER TYPE metodo_pago RENAME TO metodo_pago_old")
    op.execute(f"CREATE TYPE metodo_pago AS ENUM ({valores})")
    op.execute(
        "ALTER TABLE ventas ALTER COLUMN metodo_pago TYPE metodo_pago "
        "USING metodo_pago::text::metodo_pago"
    )
    op.execute("DROP TYPE metodo_pago_old")
