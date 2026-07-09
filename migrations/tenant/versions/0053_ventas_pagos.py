"""Pago mixto en el POS (reforma dashboard F5): una venta cobrada en varios métodos a la vez.

Piezas:
  - Valor nuevo `mixto` en el enum `metodo_pago` (ADD VALUE: no es un tipo nuevo — el guard de
    `tests/test_migrations.py` sigue en 44 enums).
  - Tabla `ventas_pagos`: las partes del cobro de una venta MIXTA (metodo + monto). Solo las ventas
    con `metodo_pago = 'mixto'` escriben filas; las normales siguen exactamente igual. La suma de
    las partes == total de la venta (lo valida el servicio). `fiado` NO participa del mixto (v1):
    el crédito tiene su propio ledger.
    Guard: `tests/test_schema_paridad.py` pasa de 91 → 92 tablas (actualizado en este PR).

Consumidores: el arqueo de caja suma la porción EFECTIVO de las mixtas (el cajón físico solo recibe
esa parte) y los reportes por método expanden la mixta en sus partes (nada aparece como "mixto" en
los desgloses de dinero).

Backward-compatible: tabla nueva vacía + valor de enum aditivo.

Revision ID: 0053_ventas_pagos
Revises: 0052_pedidos_proveedor
Create Date: 2026-07-09

Salvedad de `downgrade` (uso DEV): retira la tabla; el VALOR de enum no se puede quitar en Postgres
sin recrear el tipo — se queda (inofensivo: ninguna fila lo usa tras revertir).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision: str = "0053_ventas_pagos"
down_revision: str | None = "0052_pedidos_proveedor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

metodo_pago = ENUM(name="metodo_pago", create_type=False)


def upgrade() -> None:
    op.execute("ALTER TYPE metodo_pago ADD VALUE IF NOT EXISTS 'mixto'")
    op.create_table(
        "ventas_pagos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "venta_id",
            sa.BigInteger,
            sa.ForeignKey("ventas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metodo", metodo_pago, nullable=False),
        sa.Column("monto", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_ventas_pagos_venta", "ventas_pagos", ["venta_id"])


def downgrade() -> None:
    op.drop_index("ix_ventas_pagos_venta", table_name="ventas_pagos")
    op.drop_table("ventas_pagos")
    # El valor 'mixto' del enum queda (ver salvedad del docstring).
