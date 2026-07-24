"""Cola de impresión térmica (R1 Restaurante Ronda 2, ADR 0033 D2).

`trabajos_impresion`: un trabajo por ticket a imprimir (comanda por zona, precuenta, comprobante),
con payload JSONB DETERMINISTA (el agente no consulta negocio), estados
pendiente → entregado_agente → impreso | error, e `idempotency_key` UNIQUE — el guardarraíl
central: una comanda jamás se imprime dos veces por un reintento.

Aditivo y NULL-safe (tabla vacía no cuesta en los demás verticales).

Revision ID: 0064_trabajos_impresion
Revises: 0063_recetas_impuestos
Create Date: 2026-07-24
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0064_trabajos_impresion"
down_revision: str | None = "0063_recetas_impuestos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trabajos_impresion",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("tipo", sa.Text, nullable=False),   # comanda | precuenta | comprobante
        sa.Column("payload", JSONB, nullable=False),
        sa.Column(
            "zona_id", sa.BigInteger,
            sa.ForeignKey("comanda_zonas.id", ondelete="SET NULL"), nullable=True,
        ),
        # Ancho sugerido (80|58); NULL = lo decide el perfil local de impresora del agente (D4).
        sa.Column("ancho", sa.SmallInteger, nullable=True),
        sa.Column("estado", sa.Text, nullable=False, server_default="pendiente"),
        sa.Column("intentos", sa.SmallInteger, nullable=False, server_default="0"),
        sa.Column("error_detalle", sa.Text, nullable=True),
        # Origen NULL-safe: qué documento imprime este trabajo.
        sa.Column(
            "pedido_id", sa.BigInteger,
            sa.ForeignKey("pedidos.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "comanda_id", sa.BigInteger,
            sa.ForeignKey("comandas.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column(
            "venta_id", sa.BigInteger,
            sa.ForeignKey("ventas.id", ondelete="SET NULL"), nullable=True,
        ),
        # Reimpresión: trabajo NUEVO ligado al original (auditable).
        sa.Column(
            "reimpresion_de", sa.BigInteger,
            sa.ForeignKey("trabajos_impresion.id", ondelete="SET NULL"), nullable=True,
        ),
        sa.Column("idempotency_key", sa.Text, nullable=False, unique=True),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("entregado_en", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("impreso_en", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # La cola del agente barre por estado (pendientes + entregados vencidos).
    op.create_index("ix_trabajos_impresion_estado", "trabajos_impresion", ["estado"])


def downgrade() -> None:
    op.drop_index("ix_trabajos_impresion_estado", table_name="trabajos_impresion")
    op.drop_table("trabajos_impresion")
