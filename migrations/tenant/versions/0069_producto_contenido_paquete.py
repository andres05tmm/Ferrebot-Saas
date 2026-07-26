"""Tamaño del empaque como DATO del producto: la bolsa de cal de 25 kg.

Hasta ahora el divisor para pasar de la unidad de compra a la de venta era una CONVENCIÓN por unidad
de medida (caja de puntilla = 500 g, rollo de lija = 100 cm, tarro de tinte = 1000 ml). Eso no cubre
la mercancía que se compra por bulto y se menudea: una bolsa de cal trae 25 kg, un bulto de cemento
50, un cuñete 5 galones — el tamaño es del PRODUCTO, no de la unidad.

- `contenido_paquete`: cuántas unidades de venta trae el empaque (25 para la bolsa de cal).
- `nombre_paquete`: cómo lo llama el negocio ('bolsa', 'bulto', 'caja', 'rollo', 'tarro', 'cuñete').

NULL = el producto no se compra por empaque (o aplica la convención por unidad, que se conserva como
respaldo). El backfill deja los granel actuales con su convención hecha dato, para que nada cambie de
comportamiento: GRM/Gramos → 500 'caja', Cms → 100 'rollo', MLT/ML/Mililitros → 1000 'tarro'.

Aditiva (columnas NULL). Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0069_producto_contenido_paquete
Revises: 0068_pagos_proveedor_mixtos
Create Date: 2026-07-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069_producto_contenido_paquete"
down_revision: str | None = "0068_pagos_proveedor_mixtos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("productos", sa.Column("contenido_paquete", sa.Numeric(12, 3)))
    op.add_column("productos", sa.Column("nombre_paquete", sa.Text))
    # La convención vigente, hecha dato: mismo comportamiento, ahora editable por producto.
    op.execute(
        """
        UPDATE productos SET contenido_paquete = 500, nombre_paquete = 'caja'
         WHERE lower(btrim(unidad_medida)) IN ('grm', 'gramos') AND contenido_paquete IS NULL
        """
    )
    op.execute(
        """
        UPDATE productos SET contenido_paquete = 100, nombre_paquete = 'rollo'
         WHERE lower(btrim(unidad_medida)) = 'cms' AND contenido_paquete IS NULL
        """
    )
    op.execute(
        """
        UPDATE productos SET contenido_paquete = 1000, nombre_paquete = 'tarro'
         WHERE lower(btrim(unidad_medida)) IN ('mlt', 'ml', 'mililitros') AND contenido_paquete IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("productos", "nombre_paquete")
    op.drop_column("productos", "contenido_paquete")
