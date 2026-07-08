"""Vertical construcción — cotización AIU y obra (grupo 2 del plan PIM §3): cotizaciones_obra,
items_cotizacion_obra, obras y reportes_diarios_obra + sus enums.

Segunda migración del vertical. Solo CREATE (backward-compatible): no toca ninguna tabla existente
salvo por FKs hacia `clientes` (que ya existe), así que se aplica a TODAS las empresas vía
`tools.migrate_tenants` sin costo para las que no usan el vertical. Los literales de los enums se
conservan EXACTOS a la spec del cliente (01_MODELO_DATOS / 03 / 04, en mayúsculas). Dinero en
NUMERIC(18,4) (MONEY4); cantidades/m²/m³ en NUMERIC(18,4) (la spec declara TODO Decimal como 18,4);
porcentajes AIU en NUMERIC(6,4).

`origen_registro` (MANUAL/TELEGRAM_BOT/IMPORTACION) NACE aquí porque `reportes_diarios_obra` es la
primera tabla que lo usa (default TELEGRAM_BOT); las tablas de operación de 0045 (horas/asistencia) lo
REUSAN sin recrearlo (create_type=False) y NO lo dropean en su downgrade — es dueño esta migración.

Orden de creación por dependencias: cotizaciones_obra → items_cotizacion_obra (FK cascade) →
obras (FK 1-1 a la cotización + FK a clientes) → reportes_diarios_obra (FK a obras).

Revision ID: 0044_construccion_obra
Revises: 0043_construccion_base
Create Date: 2026-07-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0044_construccion_obra"
down_revision: str | None = "0043_construccion_base"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY4 = sa.Numeric(18, 4)     # dinero (spec @db.Decimal(18,4))
_CANTIDAD = sa.Numeric(18, 4)   # cantidades / horas / m² / m³ (misma precisión que la spec)
_PCT = sa.Numeric(6, 4)         # porcentaje AIU como fracción 0–1 (0.19)

# Enums (literales EXACTOS a la spec del cliente).
_ESTADO_COTIZACION = ("BORRADOR", "ENVIADA", "GANADA", "PERDIDA", "VENCIDA")
_ESTADO_OBRA = ("PLANIFICADA", "EN_EJECUCION", "SUSPENDIDA", "FINALIZADA", "LIQUIDADA")
_ORIGEN_REGISTRO = ("MANUAL", "TELEGRAM_BOT", "IMPORTACION")


def upgrade() -> None:
    for nombre, valores in (
        ("estado_cotizacion", _ESTADO_COTIZACION),
        ("estado_obra", _ESTADO_OBRA),
        ("origen_registro", _ORIGEN_REGISTRO),
    ):
        etiquetas = ", ".join(f"'{v}'" for v in valores)
        op.execute(f"CREATE TYPE {nombre} AS ENUM ({etiquetas})")

    # cotizaciones_obra — cotización por AIU (IVA solo sobre la utilidad). `numero` único (PIM-0XX-AAAA).
    op.create_table(
        "cotizaciones_obra",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("numero", sa.Text, nullable=False, unique=True),   # ej. "PIM-001-2026"
        sa.Column(
            "cliente_id", sa.BigInteger, sa.ForeignKey("clientes.id"), nullable=False
        ),
        sa.Column("nombre_obra", sa.Text, nullable=False),
        sa.Column("ubicacion", sa.Text),
        sa.Column(
            "fecha_emision", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("vigencia_dias", sa.Integer, nullable=False, server_default="15"),
        # Porcentajes AIU (fracción 0–1). El IVA de la cotización recae SOLO sobre la utilidad.
        sa.Column("administracion_pct", _PCT, nullable=False, server_default="0"),
        sa.Column("imprevistos_pct", _PCT, nullable=False, server_default="0"),
        sa.Column("utilidad_pct", _PCT, nullable=False, server_default="0"),
        sa.Column("iva_sobre_utilidad_pct", _PCT, nullable=False, server_default="0.19"),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADO_COTIZACION, name="estado_cotizacion", create_type=False),
            nullable=False, server_default="BORRADOR",
        ),
        sa.Column("condiciones", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_cotizaciones_obra_cliente_id", "cotizaciones_obra", ["cliente_id"])

    # items_cotizacion_obra — renglones de la cotización; se borran en cascada con ella (spec Cascade).
    op.create_table(
        "items_cotizacion_obra",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "cotizacion_id", sa.BigInteger,
            sa.ForeignKey("cotizaciones_obra.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("orden", sa.Integer, nullable=False),
        sa.Column("descripcion", sa.Text, nullable=False),
        sa.Column("unidad", sa.Text, nullable=False),
        sa.Column("cantidad", _CANTIDAD, nullable=False),
        sa.Column("valor_unitario", _MONEY4, nullable=False),
        # Desglose de costo interno estimado (para presupuesto vs. real de la obra). Nullable.
        sa.Column("costo_material_est", _MONEY4),
        sa.Column("costo_mano_obra_est", _MONEY4),
        sa.Column("costo_equipo_est", _MONEY4),
    )
    op.create_index(
        "ix_items_cotizacion_obra_cotizacion_id", "items_cotizacion_obra", ["cotizacion_id"]
    )

    # obras — nace de una cotización GANADA. `cotizacion_id` es 1-1 (UNIQUE) y nullable (plan PIM: la
    # obra normalmente proviene de una cotización, pero la FK se deja opcional). `eliminado_en` habilita
    # el soft delete que exige el contrato de CRUD de la fase (DELETE /obras).
    op.create_table(
        "obras",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "cotizacion_id", sa.BigInteger,
            sa.ForeignKey("cotizaciones_obra.id"), unique=True,   # 1-1; NULL permitido (UNIQUE ignora NULLs)
        ),
        sa.Column(
            "cliente_id", sa.BigInteger, sa.ForeignKey("clientes.id"), nullable=False
        ),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("ubicacion", sa.Text),
        sa.Column("fecha_inicio", sa.Date),
        sa.Column("fecha_fin_estimada", sa.Date),
        sa.Column("fecha_fin_real", sa.Date),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADO_OBRA, name="estado_obra", create_type=False),
            nullable=False, server_default="PLANIFICADA",
        ),
        sa.Column("notas", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("eliminado_en", sa.TIMESTAMP(timezone=True)),  # soft delete (contrato CRUD de la fase)
    )
    op.create_index("ix_obras_cliente_id", "obras", ["cliente_id"])

    # reportes_diarios_obra — bitácora de avance (viene del bot Telegram o manual). Primera tabla que
    # usa `origen_registro`; `foto_urls` es un arreglo de URLs (spec String[], default arreglo vacío).
    op.create_table(
        "reportes_diarios_obra",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "obra_id", sa.BigInteger, sa.ForeignKey("obras.id"), nullable=False
        ),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("reportado_por", sa.Text),
        sa.Column("telegram_user_id", sa.Text),
        sa.Column("avance_descripcion", sa.Text),
        sa.Column("m2_ejecutados", _CANTIDAD),
        sa.Column("m3_ejecutados", _CANTIDAD),
        sa.Column("incidentes", sa.Text),
        sa.Column(
            "foto_urls", postgresql.ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'::text[]")
        ),
        sa.Column(
            "origen_registro",
            postgresql.ENUM(*_ORIGEN_REGISTRO, name="origen_registro", create_type=False),
            nullable=False, server_default="TELEGRAM_BOT",
        ),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_reportes_diarios_obra_obra_id", "reportes_diarios_obra", ["obra_id"])


def downgrade() -> None:
    # Tablas en orden inverso (drop_table lleva sus índices), luego los tipos enum en orden inverso.
    op.drop_table("reportes_diarios_obra")
    op.drop_table("obras")
    op.drop_table("items_cotizacion_obra")
    op.drop_table("cotizaciones_obra")
    for nombre in ("origen_registro", "estado_obra", "estado_cotizacion"):
        op.execute(f"DROP TYPE {nombre}")
