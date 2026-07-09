"""Pedidos a proveedor con cronómetro de lead time + inventario progresivo (reforma dashboard POS F2).

Piezas:
  - Tabla `pedidos_proveedor`: la orden al proveedor ANTES de que llegue la mercancía. El cronómetro
    es `fecha_recepcion − fecha_pedido` (derivado en lectura, no columna). Estados: pedido → recibido |
    cancelado. `anticipo` registra el pago por adelantado (hay proveedores que cobran al pedir);
    `anticipo_movimiento_id` ancla el egreso de caja que lo pagó. Al recibir, el service crea la compra
    real (`compra_id`) y, si es a crédito, la cuenta por pagar (`factura_proveedor_id`).
    `ultimo_aviso_at` es el dedup del cron de pedidos demorados (patrón `pagar_avisos`).
  - Tabla `pedidos_proveedor_detalle`: líneas OPCIONALES del pedido (captura flexible: proveedor +
    descripción + monto estimado basta; el detalle preciso llega con la mercancía). `producto_id` NULL
    permite pedir "lo de siempre" sin catálogo.
  - Enums nuevos: `pedido_prov_estado` y `pedido_prov_condicion` (contado | credito | anticipado).
    OJO guard: `tests/test_migrations.py` pasa de 42 → 44 enums y `tests/test_schema_paridad.py` de
    89 → 91 tablas (actualizados en esta misma migración/PR).
  - ALTER `inventario` ADD `cuadrado_at` (TIMESTAMPTZ NULL): inventario progresivo — sello del último
    CUADRE físico del producto (conteo set-to-absolute). NULL = nunca cuadrado (stock no confiable:
    el negocio arranca sin inventario y vende en negativo); los reportes de stock bajo / valor de
    inventario solo confían en productos cuadrados. Lo sella `InventarioService.contar`.

Backward-compatible: tablas nuevas vacías + columna nullable; el acceso lo gatea la feature nueva
`pedidos_proveedor` (dep `inventario`). Se aplica a todas las empresas vía `tools.migrate_tenants`.

Dinero en MONEY (12,2) y cantidades en QTY (12,3), consistente con compras/POS.

Revision ID: 0052_pedidos_proveedor
Revises: 0051_colita_dedup_asistencia_uq
Create Date: 2026-07-09
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision: str = "0052_pedidos_proveedor"
down_revision: str | None = "0051_colita_dedup_asistencia_uq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

pedido_prov_estado = ENUM(
    "pedido", "recibido", "cancelado", name="pedido_prov_estado", create_type=False
)
pedido_prov_condicion = ENUM(
    "contado", "credito", "anticipado", name="pedido_prov_condicion", create_type=False
)


def upgrade() -> None:
    pedido_prov_estado.create(op.get_bind(), checkfirst=True)
    pedido_prov_condicion.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "pedidos_proveedor",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "proveedor_id",
            sa.BigInteger,
            sa.ForeignKey("proveedores.id"),
            nullable=False,
        ),
        sa.Column("fecha_pedido", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("fecha_estimada", sa.Date),
        sa.Column(
            "estado", pedido_prov_estado, nullable=False, server_default="pedido"
        ),
        sa.Column("descripcion", sa.Text),
        sa.Column("monto_estimado", sa.Numeric(12, 2)),
        # Pago por adelantado (proveedores que cobran al pedir): monto + ancla del egreso de caja.
        sa.Column("anticipo", sa.Numeric(12, 2)),
        sa.Column("anticipo_movimiento_id", sa.BigInteger),
        sa.Column("fecha_recepcion", sa.TIMESTAMP(timezone=True)),
        sa.Column("compra_id", sa.BigInteger, sa.ForeignKey("compras.id", ondelete="SET NULL")),
        sa.Column(
            "factura_proveedor_id",
            sa.Text,
            sa.ForeignKey("facturas_proveedores.id", ondelete="SET NULL"),
        ),
        sa.Column("condicion_pago", pedido_prov_condicion),
        sa.Column("usuario_id", sa.BigInteger, sa.ForeignKey("usuarios.id", ondelete="SET NULL")),
        sa.Column("notas", sa.Text),
        sa.Column("idempotency_key", sa.Text),
        # Dedup del cron de pedidos demorados (Fase 6, patrón pagar_avisos.ultimo_aviso_en).
        sa.Column("ultimo_aviso_at", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "creado_en",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Idempotencia estructural del alta (mismo patrón que compras 0025): UNIQUE parcial.
    op.create_index(
        "uq_pedidos_proveedor_idem",
        "pedidos_proveedor",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # La lista viva y el cron barren SOLO los pedidos en camino: índice parcial por estado.
    op.create_index(
        "ix_pedidos_proveedor_en_camino",
        "pedidos_proveedor",
        ["fecha_pedido"],
        postgresql_where=sa.text("estado = 'pedido'"),
    )

    op.create_table(
        "pedidos_proveedor_detalle",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "pedido_id",
            sa.BigInteger,
            sa.ForeignKey("pedidos_proveedor.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("producto_id", sa.BigInteger, sa.ForeignKey("productos.id")),
        sa.Column("descripcion", sa.Text),
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
        sa.Column("costo_estimado", sa.Numeric(12, 2)),
    )
    op.create_index(
        "ix_pedidos_proveedor_detalle_pedido",
        "pedidos_proveedor_detalle",
        ["pedido_id"],
    )

    # Inventario progresivo: sello del último cuadre físico (NULL = stock aún no confiable).
    op.add_column("inventario", sa.Column("cuadrado_at", sa.TIMESTAMP(timezone=True)))


def downgrade() -> None:
    # Simétrico, uso DEV (base efímera): orden inverso; los enums se retiran al final.
    op.drop_column("inventario", "cuadrado_at")
    op.drop_index("ix_pedidos_proveedor_detalle_pedido", table_name="pedidos_proveedor_detalle")
    op.drop_table("pedidos_proveedor_detalle")
    op.drop_index("ix_pedidos_proveedor_en_camino", table_name="pedidos_proveedor")
    op.drop_index("uq_pedidos_proveedor_idem", table_name="pedidos_proveedor")
    op.drop_table("pedidos_proveedor")
    pedido_prov_condicion.drop(op.get_bind(), checkfirst=True)
    pedido_prov_estado.drop(op.get_bind(), checkfirst=True)
