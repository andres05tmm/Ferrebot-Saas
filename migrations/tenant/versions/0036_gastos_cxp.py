"""Gastos ↔ cuentas por pagar (ADR 0028): un gasto puede SALDAR una factura de proveedor.

Enlace ADITIVO y opcional sobre `gastos` (todas las columnas nullable → segura sobre datos existentes):

- `proveedor_id` (BIGINT, FK proveedores): a quién se le pagó (desplegable), independiente de si salda
  una factura formal.
- `factura_proveedor_id` (TEXT, FK facturas_proveedores): la cuenta por pagar que este gasto salda.
- `abono_proveedor_id` (BIGINT, FK facturas_abonos): el ÚNICO abono que ESTE gasto generó al saldar la
  factura. Es el candado anti-duplicación: el gasto crea su abono (recalcula `pendiente`) y guarda su
  id aquí; la idempotencia del gasto impide un segundo abono. Un gasto → a lo sumo UN abono.

Semántica (ver ADR 0028): el gasto ya postea su egreso de caja (libro de CAJA); el abono reduce
`facturas_proveedores.pendiente` (libro de CxP). Son dos libros del MISMO pago, no un doble cobro.

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0036_gastos_cxp
Revises: 0035_bancos_conciliacion
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_gastos_cxp"
down_revision: str | None = "0035_bancos_conciliacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "gastos",
        sa.Column(
            "proveedor_id", sa.BigInteger,
            sa.ForeignKey("proveedores.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.add_column(
        "gastos",
        sa.Column(
            "factura_proveedor_id", sa.Text,
            sa.ForeignKey("facturas_proveedores.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.add_column(
        "gastos",
        sa.Column(
            "abono_proveedor_id", sa.BigInteger,
            sa.ForeignKey("facturas_abonos.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("gastos", "abono_proveedor_id")
    op.drop_column("gastos", "factura_proveedor_id")
    op.drop_column("gastos", "proveedor_id")
