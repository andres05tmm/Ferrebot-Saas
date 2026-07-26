"""Ciclo de compra pedido→recepción→corrección, con la plata trazada.

- `compras.corregida_en` / `correcciones` / `nota_correccion`: una compra recibida se puede corregir
  (el dueño se equivoca al digitar cantidades o costos). `correcciones` es el contador que entra en
  la key natural de la n-ésima corrección: sin él, la segunda corrección haría *replay* de la
  primera y no aplicaría nada. `ultima_correccion_key` guarda la última `Idempotency-Key` usada
  (replay del doble clic vs conflicto con otro payload).
- `pedidos_proveedor.origen_anticipo` / `origen_pago`: de dónde salió cada pago al proveedor —
  `caja` (mueve el cajón del día), `efectivo_externo` (efectivo guardado de días anteriores) o
  `banco`. Solo `caja` postea movimiento de caja; los otros dos quedan registrados para que el
  reporte de egresos sepa la procedencia sin descuadrar el arqueo.
- `facturas_abonos.origen_fondos` / `caja_movimiento_id`: lo mismo para los abonos a cuentas por
  pagar, que hasta hoy NUNCA movían caja (descuadraba el arqueo al pagar con el efectivo del cajón).

Aditiva (columnas NULL o con default). Se aplica a TODAS las empresas vía `tools.migrate_tenants`.

Revision ID: 0067_compras_correccion_y_origen
Revises: 0066_cliente_tipo_persona
Create Date: 2026-07-25
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_compras_correccion_y_origen"
down_revision: str | None = "0066_cliente_tipo_persona"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("compras", sa.Column("corregida_en", sa.DateTime(timezone=True)))
    op.add_column(
        "compras", sa.Column("correcciones", sa.Integer, nullable=False, server_default="0")
    )
    op.add_column("compras", sa.Column("nota_correccion", sa.Text))
    op.add_column("compras", sa.Column("ultima_correccion_key", sa.Text))
    # Vínculo compra → cuenta por pagar: hasta ahora el puente `a_credito` creaba la factura y no
    # dejaba rastro de cuál era (solo se adivinaba por el id 'COMPRA-{id}'). Corregir una compra a
    # crédito necesita saber qué deuda mover.
    op.add_column("compras", sa.Column("factura_proveedor_id", sa.Text))

    op.add_column("pedidos_proveedor", sa.Column("origen_anticipo", sa.Text))
    op.add_column("pedidos_proveedor", sa.Column("origen_pago", sa.Text))

    op.add_column("facturas_abonos", sa.Column("origen_fondos", sa.Text))
    op.add_column("facturas_abonos", sa.Column("caja_movimiento_id", sa.BigInteger))


def downgrade() -> None:
    op.drop_column("facturas_abonos", "caja_movimiento_id")
    op.drop_column("facturas_abonos", "origen_fondos")
    op.drop_column("pedidos_proveedor", "origen_pago")
    op.drop_column("pedidos_proveedor", "origen_anticipo")
    op.drop_column("compras", "factura_proveedor_id")
    op.drop_column("compras", "ultima_correccion_key")
    op.drop_column("compras", "nota_correccion")
    op.drop_column("compras", "correcciones")
    op.drop_column("compras", "corregida_en")
