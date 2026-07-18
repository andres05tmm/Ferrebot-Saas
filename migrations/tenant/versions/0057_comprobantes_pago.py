"""Comprobantes de pago (foto que manda el cliente) — desempate de la conciliación (demo Sirius).

El cliente manda por su canal la FOTO del comprobante de la transferencia. Visión la lee
(`ai/vision/recibo.py` → `ReciboExtraido`) y aquí se registra la fila de auditoría en
`comprobantes_pago`, asociándola (sin marcar pagado — una captura es falsificable) al cobro
`pendiente` de pedido del mismo cliente. Cuando después entra la transferencia REAL y el
conciliador encuentra ≥2 cobros con el mismo monto, el comprobante asociado desempata: paga el
cobro que tiene comprobante.

`cobro_id` es una FK LÓGICA a `cobros` (sin constraint): el comprobante puede llegar antes de
resolver el cobro, o no casar con ninguno, y aun así se guarda para auditoría.

Solo creación de tabla (aditivo). Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0057_comprobantes_pago
Revises: 0056_gasto_rechazo
Create Date: 2026-07-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_comprobantes_pago"
down_revision: str | None = "0056_gasto_rechazo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comprobantes_pago",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_telefono", sa.Text, nullable=False),
        sa.Column("cobro_id", sa.BigInteger),          # FK lógica a cobros (NULL si no casó)
        sa.Column("monto", sa.Numeric(12, 2)),
        sa.Column("fecha", sa.Date),
        sa.Column("referencia", sa.Text),
        sa.Column("origen", sa.Text),                  # entidad_o_producto_origen del recibo
        sa.Column("destino", sa.Text),
        sa.Column("banco_tipo", sa.Text),              # tipo_transaccion del recibo
        sa.Column("confianza", sa.Numeric),
        sa.Column("imagen_ref", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # Desempate del conciliador: ¿qué cobros tienen comprobante? → índice por cobro_id.
    op.create_index("ix_comprobantes_cobro", "comprobantes_pago", ["cobro_id"])
    # Matching por cliente en la ventana temporal.
    op.create_index(
        "ix_comprobantes_cliente_creado", "comprobantes_pago", ["cliente_telefono", "creado_en"]
    )


def downgrade() -> None:
    op.drop_index("ix_comprobantes_cliente_creado", table_name="comprobantes_pago")
    op.drop_index("ix_comprobantes_cobro", table_name="comprobantes_pago")
    op.drop_table("comprobantes_pago")
