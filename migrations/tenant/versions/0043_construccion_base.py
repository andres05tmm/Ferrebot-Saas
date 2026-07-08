"""Vertical construcción — modelos base (grupo 1 del plan PIM §3): parámetros legales, máquinas,
herramientas y trabajadores + sus enums.

Primera migración del vertical construcción (Construcciones PIM). Solo CREATE (backward-compatible): no
toca ninguna tabla existente, así que se aplica a TODAS las empresas vía `tools.migrate_tenants`
(tenancy.md §7) sin costo para las que no usan el vertical (tabla vacía no pesa; el acceso lo controlan
las feature flags). Los literales de los enums se conservan EXACTOS como en la spec del cliente
(01/05/06/07, en mayúsculas). Dinero en NUMERIC(18,4) (MONEY4, divergencia documentada en core/money.py);
porcentajes de nómina en NUMERIC(6,4).

Orden: `trabajadores` antes que `maquinas` porque `maquinas.operador_asignado_id` referencia a aquélla.

Revision ID: 0043_construccion_base
Revises: 0042_cufe_recibidas_unico
Create Date: 2026-07-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0043_construccion_base"
down_revision: str | None = "0042_cufe_recibidas_unico"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY4 = sa.Numeric(18, 4)   # dinero del vertical construcción (spec @db.Decimal(18,4))
_PCT = sa.Numeric(6, 4)       # porcentaje como fracción 0–1 con 4 decimales (0.0833, 0.0417)

# Enums (literales EXACTOS a la spec del cliente).
_TIPO_VINCULACION = ("DIRECTO", "PATACALIENTE")
_ESTADO_MAQUINA = ("DISPONIBLE", "OCUPADA", "MANTENIMIENTO", "DAÑADA", "BAJA")
_ESTADO_HERRAMIENTA = ("DISPONIBLE", "EN_OBRA", "MANTENIMIENTO", "PERDIDA", "BAJA")


def upgrade() -> None:
    for nombre, valores in (
        ("tipo_vinculacion", _TIPO_VINCULACION),
        ("estado_maquina", _ESTADO_MAQUINA),
        ("estado_herramienta", _ESTADO_HERRAMIENTA),
    ):
        etiquetas = ", ".join(f"'{v}'" for v in valores)
        op.execute(f"CREATE TYPE {nombre} AS ENUM ({etiquetas})")

    # parametros_legales — set de valores legales vigente por rango de fechas (nómina, Fase 4).
    op.create_table(
        "parametros_legales",
        sa.Column("id", sa.Integer, primary_key=True),
        # UNIQUE: una fila por periodo de vigencia. Habilita el UPSERT idempotente del loader
        # (`ON CONFLICT (vigente_desde)`, cargar_construccion) — invariante de idempotencia del provisioning.
        sa.Column("vigente_desde", sa.Date, nullable=False, unique=True),
        sa.Column("vigente_hasta", sa.Date),  # NULL = vigente actual
        sa.Column("smmlv", _MONEY4, nullable=False),
        sa.Column("auxilio_transporte", _MONEY4, nullable=False),
        sa.Column(
            "auxilio_transporte_tope_smmlv", sa.Integer, nullable=False, server_default="2"
        ),
        sa.Column("salud_empleado_pct", _PCT, nullable=False),
        sa.Column("pension_empleado_pct", _PCT, nullable=False),
        sa.Column("salud_empleador_pct", _PCT, nullable=False),
        sa.Column("pension_empleador_pct", _PCT, nullable=False),
        sa.Column("arl_pct", _PCT),  # varía por clase de riesgo [DEFINIR]
        sa.Column("caja_compensacion_pct", _PCT, nullable=False, server_default="0.04"),
        sa.Column("sena_pct", _PCT, nullable=False, server_default="0.02"),
        sa.Column("icbf_pct", _PCT, nullable=False, server_default="0.03"),
        sa.Column("cesantias_pct", _PCT, nullable=False, server_default="0.0833"),
        sa.Column("intereses_cesantias_pct", _PCT, nullable=False, server_default="0.01"),
        sa.Column("prima_pct", _PCT, nullable=False, server_default="0.0833"),
        sa.Column("vacaciones_pct", _PCT, nullable=False, server_default="0.0417"),
        sa.Column("iva_general", _PCT, nullable=False, server_default="0.19"),
        sa.Column("notas", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )

    # trabajadores — se crea ANTES que maquinas (FK operador_asignado_id).
    op.create_table(
        "trabajadores",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "tipo_vinculacion",
            postgresql.ENUM(*_TIPO_VINCULACION, name="tipo_vinculacion", create_type=False),
            nullable=False,
        ),
        sa.Column("documento", sa.Text, nullable=False, unique=True),
        sa.Column("tipo_documento", sa.Text, nullable=False, server_default="CC"),
        sa.Column("nombres", sa.Text, nullable=False),
        sa.Column("apellidos", sa.Text, nullable=False),
        sa.Column("telefono", sa.Text),
        sa.Column("email", sa.Text),
        sa.Column("direccion", sa.Text),
        sa.Column("cargo", sa.Text, nullable=False),
        sa.Column("fecha_ingreso", sa.Date),
        sa.Column("fecha_retiro", sa.Date),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("salario_base", _MONEY4),
        sa.Column("aplica_aux_transporte", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("eps", sa.Text),
        sa.Column("fondo_pension", sa.Text),
        sa.Column("arl", sa.Text),
        sa.Column("caja_compensacion", sa.Text),
        sa.Column("cuenta_bancaria", sa.Text),
        sa.Column("banco_nombre", sa.Text),
        sa.Column("tarifa_hora", _MONEY4),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("eliminado_en", sa.TIMESTAMP(timezone=True)),  # soft delete
    )

    # maquinas — activos facturados por hora; operador_asignado_id → trabajadores.id.
    op.create_table(
        "maquinas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("codigo", sa.Text, nullable=False, unique=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("tipo", sa.Text, nullable=False),
        sa.Column("placa", sa.Text),
        sa.Column("serial", sa.Text),
        sa.Column("anio_fabricacion", sa.Integer),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADO_MAQUINA, name="estado_maquina", create_type=False),
            nullable=False, server_default="DISPONIBLE",
        ),
        sa.Column("precio_hora_default", _MONEY4, nullable=False),
        sa.Column("minimo_horas_factura", sa.Integer, nullable=False, server_default="1"),
        sa.Column("costo_operacion_hora", _MONEY4),  # [DEFINIR] rentabilidad neta
        sa.Column(
            "operador_asignado_id", sa.BigInteger,
            sa.ForeignKey("trabajadores.id", ondelete="SET NULL"),
        ),
        sa.Column("foto_url", sa.Text),
        sa.Column("notas", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("eliminado_en", sa.TIMESTAMP(timezone=True)),  # soft delete
    )

    # herramientas — CRUD ligero.
    op.create_table(
        "herramientas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("codigo", sa.Text, nullable=False, unique=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("categoria", sa.Text),
        sa.Column("cantidad", sa.Integer, nullable=False, server_default="1"),
        sa.Column("ubicacion_actual", sa.Text),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADO_HERRAMIENTA, name="estado_herramienta", create_type=False),
            nullable=False, server_default="DISPONIBLE",
        ),
        sa.Column("valor_reposicion", _MONEY4),
        sa.Column("notas", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("eliminado_en", sa.TIMESTAMP(timezone=True)),  # soft delete
    )


def downgrade() -> None:
    # Tablas en orden inverso (maquinas antes que trabajadores por la FK), luego los tipos enum.
    op.drop_table("herramientas")
    op.drop_table("maquinas")
    op.drop_table("trabajadores")
    op.drop_table("parametros_legales")
    for nombre in ("estado_herramienta", "estado_maquina", "tipo_vinculacion"):
        op.execute(f"DROP TYPE {nombre}")
