"""Pago MIXTO a proveedor: una parte en efectivo y otra por transferencia.

Espeja `ventas_pagos` (0053) del lado del dinero que SALE: un pago al proveedor —al pedir, al
recibir o al abonar una cuenta por pagar— puede repartirse entre medios. `pagos_proveedor` guarda
las partes (`origen` + `monto`) colgadas del hecho que las originó (`ref_tipo`/`ref_id`).

Solo la parte con `origen='caja'` postea movimiento de caja (y queda enlazada por
`caja_movimiento_id`): el arqueo del cajón sigue cuadrando. Las otras partes son plata que salió del
negocio sin pasar por la caja (efectivo guardado de días anteriores, transferencia) y hasta ahora no
se podían registrar: el campo era un solo texto sin monto.

Backward-compatible: tabla nueva vacía. Un pago de un solo medio sigue funcionando igual (y también
escribe su parte, para que el reporte de egresos no tenga dos caminos).

Revision ID: 0068_pagos_proveedor_mixtos
Revises: 0067_compras_correccion_y_origen
Create Date: 2026-07-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068_pagos_proveedor_mixtos"
down_revision: str | None = "0067_compras_correccion_y_origen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pagos_proveedor",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # Qué hecho pagó: 'pedido' (pago al pedir), 'compra' (recepción o corrección) o 'abono'.
        sa.Column("ref_tipo", sa.Text, nullable=False),
        sa.Column("ref_id", sa.BigInteger, nullable=False),
        sa.Column("origen", sa.Text, nullable=False),   # caja | efectivo_externo | banco
        sa.Column("monto", sa.Numeric(12, 2), nullable=False),
        sa.Column("caja_movimiento_id", sa.BigInteger),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_pagos_proveedor_ref", "pagos_proveedor", ["ref_tipo", "ref_id"])
    op.create_index("ix_pagos_proveedor_creado", "pagos_proveedor", ["creado_en"])


def downgrade() -> None:
    op.drop_index("ix_pagos_proveedor_creado", table_name="pagos_proveedor")
    op.drop_index("ix_pagos_proveedor_ref", table_name="pagos_proveedor")
    op.drop_table("pagos_proveedor")
