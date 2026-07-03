"""Notas crédito/débito ligadas a la venta + devoluciones con reintegro (ADR 0026, Fase 3 Contable B).

Amplía `notas_electronicas` (existente desde 0001, ORM desde 0030) con el vínculo a la venta origen,
su propio consecutivo/prefijo DIAN, idempotencia de emisión y la respuesta cruda de MATIAS. Crea las
tablas `devoluciones` + `devoluciones_detalle`: una devolución re-ingresa mercancía al inventario
(movimiento DEVOLUCION con el costo del snapshot de la SALIDA original) y su contrapartida de caja
(egreso) o de fiado (abono), vinculada a la nota crédito cuando la venta fue facturada.

Aditiva, NULL-safe y reversible. En una base ya migrada las columnas/tablas no existen (0030 las
espeja de la 0001 sin estos campos), así que el `upgrade` las agrega y el `downgrade` las revierte
sin tocar datos de la 0001.

Revision ID: 0031_notas_devoluciones
Revises: 0030_orm_huerfanas
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0031_notas_devoluciones"
down_revision: str | None = "0030_orm_huerfanas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- notas_electronicas: vínculo a la venta + numeración/idempotencia propias -----------------
    op.add_column("notas_electronicas", sa.Column("venta_id", sa.BigInteger(), nullable=True))
    op.add_column("notas_electronicas", sa.Column("consecutivo", sa.BigInteger(), nullable=True))
    op.add_column("notas_electronicas", sa.Column("prefijo", sa.Text(), nullable=True))
    op.add_column("notas_electronicas", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.add_column("notas_electronicas", sa.Column("dian_respuesta", JSONB(), nullable=True))
    op.add_column(
        "notas_electronicas",
        sa.Column("intentos", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "notas_electronicas", sa.Column("emitido_en", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint(
        "uq_notas_idempotency", "notas_electronicas", ["idempotency_key"]
    )
    op.create_foreign_key(
        "fk_notas_venta", "notas_electronicas", "ventas", ["venta_id"], ["id"]
    )

    # --- devoluciones: cabecera del reintegro ----------------------------------------------------
    op.create_table(
        "devoluciones",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("venta_id", sa.BigInteger(), sa.ForeignKey("ventas.id"), nullable=False),
        sa.Column("nota_id", sa.BigInteger(), sa.ForeignKey("notas_electronicas.id"), nullable=True),
        sa.Column("total", sa.Numeric(12, 2), nullable=False),
        # Cómo se reintegró el dinero: 'efectivo' (egreso de caja) | 'fiado' (abono al crédito).
        sa.Column("metodo_reintegro", sa.Text(), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=True),
        sa.Column("usuario_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True, unique=True),
        sa.Column("estado", sa.Text(), nullable=False, server_default="registrada"),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_devoluciones_venta", "devoluciones", ["venta_id"])

    # --- devoluciones_detalle: líneas devueltas (con el costo del snapshot original) --------------
    op.create_table(
        "devoluciones_detalle",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "devolucion_id",
            sa.BigInteger(),
            sa.ForeignKey("devoluciones.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("producto_id", sa.BigInteger(), nullable=True),
        sa.Column("descripcion", sa.Text(), nullable=True),
        sa.Column("cantidad", sa.Numeric(12, 3), nullable=False),
        sa.Column("precio_unitario", sa.Numeric(12, 2), nullable=False),
        # Costo unitario = snapshot de la SALIDA original (COGS exacto; NO el promedio del día).
        sa.Column("costo_unitario", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_linea", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_devoluciones_detalle_dev", "devoluciones_detalle", ["devolucion_id"])


def downgrade() -> None:
    op.drop_index("ix_devoluciones_detalle_dev", table_name="devoluciones_detalle")
    op.drop_table("devoluciones_detalle")
    op.drop_index("ix_devoluciones_venta", table_name="devoluciones")
    op.drop_table("devoluciones")

    op.drop_constraint("fk_notas_venta", "notas_electronicas", type_="foreignkey")
    op.drop_constraint("uq_notas_idempotency", "notas_electronicas", type_="unique")
    for col in (
        "emitido_en", "intentos", "dian_respuesta", "idempotency_key", "prefijo",
        "consecutivo", "venta_id",
    ):
        op.drop_column("notas_electronicas", col)
