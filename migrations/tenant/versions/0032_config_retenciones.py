"""Config de retenciones/INC por tenant (ADR 0027).

Una sola tabla editable por empresa que gobierna TODO el catálogo tributario del negocio, SIN tarifas
hardcodeadas en el código: retefuente por concepto (con base mínima en UVT y tarifa %), ICA por
municipio (tarifa por mil), reteIVA (% sobre el IVA) e INC (impuesto nacional al consumo, % por tipo).
El valor del UVT en pesos vive como una fila especial (`tipo='uvt'`, `concepto` = año, `tarifa` = pesos)
para convertir la base mínima sin acoplar un valor de gobierno al código.

Semilla VACÍA por diseño (opt-in): sin filas, el motor no calcula nada y ningún total cambia. Cada fila
nace `editable=true` para que la empresa la ajuste. Tabla de negocio sin `empresa_id`: la base ES la
frontera del tenant.

Revision ID: 0032_config_retenciones
Revises: 0031_notas_devoluciones
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_config_retenciones"
down_revision: str | None = "0031_notas_devoluciones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "config_retenciones",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # 'retefuente' | 'ica' | 'reteiva' | 'inc' | 'uvt'
        sa.Column("tipo", sa.Text, nullable=False),
        # retefuente: 'compras'/'servicios'/'honorarios'; ica: municipio; inc: tipo de bien/servicio;
        # reteiva: etiqueta libre; uvt: año ('2026'). Parte de la clave natural (dedup por config).
        sa.Column("concepto", sa.Text, nullable=False),
        # Umbral en UVT bajo el cual NO se retiene (retefuente). 0 = sin mínimo (aplica siempre).
        sa.Column("base_minima_uvt", sa.Numeric(12, 2), nullable=False, server_default="0"),
        # retefuente/reteiva/inc: porcentaje (2.5 = 2.5%). ica: por mil (4.14 = 4.14×1000).
        # uvt: valor del UVT en PESOS de ese año.
        sa.Column("tarifa", sa.Numeric(9, 4), nullable=False, server_default="0"),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("editable", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tipo", "concepto", name="uq_config_retenciones_tipo_concepto"),
    )


def downgrade() -> None:
    op.drop_table("config_retenciones")
