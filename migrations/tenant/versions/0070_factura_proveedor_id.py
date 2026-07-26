"""La deuda queda ligada al PROVEEDOR, no a un nombre suelto.

`facturas_proveedores.proveedor` es texto libre desde el esquema inicial: "Ferrisariato" y
"FERRISARIATO SAS" son dos deudas distintas para el mismo señor, y la deuda no se puede cruzar con
sus pedidos ni con sus compras (que sí usan `proveedor_id`). Sin esto, el tab de proveedores no
puede mostrar el estado de cuenta de cada uno.

`proveedor_id` (FK, ON DELETE SET NULL) se llena solo cuando la factura nace de una compra o de una
recepción. El backfill enlaza el histórico por nombre normalizado (minúsculas y sin espacios de
sobra); lo que no case queda en NULL y el dashboard lo muestra aparte para asignarlo a mano — no se
inventan proveedores a partir de un texto con errores de digitación.

El campo `proveedor` (texto) se conserva: es lo que el dueño escribió y sirve de respaldo si la
factura no tiene proveedor asignado.

Revision ID: 0070_factura_proveedor_id
Revises: 0069_producto_contenido_paquete
Create Date: 2026-07-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070_factura_proveedor_id"
down_revision: str | None = "0069_producto_contenido_paquete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("facturas_proveedores", sa.Column("proveedor_id", sa.BigInteger))
    op.create_foreign_key(
        "fk_facturas_proveedores_proveedor", "facturas_proveedores", "proveedores",
        ["proveedor_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index(
        "ix_facturas_proveedores_proveedor_id", "facturas_proveedores", ["proveedor_id"]
    )
    # Backfill por nombre normalizado: solo enlaza lo que casa sin ambigüedad.
    op.execute(
        """
        UPDATE facturas_proveedores f
           SET proveedor_id = p.id
          FROM proveedores p
         WHERE f.proveedor_id IS NULL
           AND lower(btrim(f.proveedor)) = lower(btrim(p.nombre))
        """
    )


def downgrade() -> None:
    op.drop_index("ix_facturas_proveedores_proveedor_id", table_name="facturas_proveedores")
    op.drop_constraint(
        "fk_facturas_proveedores_proveedor", "facturas_proveedores", type_="foreignkey"
    )
    op.drop_column("facturas_proveedores", "proveedor_id")
