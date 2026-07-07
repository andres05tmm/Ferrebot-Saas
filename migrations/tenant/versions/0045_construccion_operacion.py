"""Vertical construcción — operación de obra (grupo 3 del plan PIM §3): asignación y horas de máquina,
mantenimientos, asignación y asistencia de trabajadores, y consumos de inventario + sus enums.

Tercera migración del vertical. Solo CREATE (backward-compatible): las FKs apuntan a tablas que ya
existen —`maquinas`/`trabajadores` (0043), `obras` (0044), `proveedores`/`productos` (0001)—; no
altera ninguna. Se aplica a TODAS las empresas vía `tools.migrate_tenants`. Literales de enums EXACTOS
a la spec (01/05/07). Dinero en NUMERIC(18,4) (MONEY4); horas/cantidades en NUMERIC(18,4); el precio y
mínimo de facturación por hora viven POR ASIGNACIÓN (`precio_hora`/`minimo_horas`), pueden diferir del
default de la máquina.

`origen_registro` NO se crea aquí: es dueño 0044 (reportes_diarios_obra). Las tablas de horas y
asistencia lo REUSAN (create_type=False) y el downgrade de esta migración NO lo dropea. `consumos_inventario`
referencia `productos` existente (spec `ItemInventario` → nuestro catálogo POS); el MOVIMIENTO de
inventario lo genera el service de Fase 3, no esta tabla (aquí solo la fila del consumo).

Revision ID: 0045_construccion_operacion
Revises: 0044_construccion_obra
Create Date: 2026-07-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_construccion_operacion"
down_revision: str | None = "0044_construccion_obra"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY4 = sa.Numeric(18, 4)     # dinero (spec @db.Decimal(18,4))
_CANTIDAD = sa.Numeric(18, 4)   # horas / cantidades / horómetro (misma precisión que la spec)

# Enums nuevos de esta migración (literales EXACTOS a la spec). `origen_registro` es de 0044.
_TIPO_MANTENIMIENTO = ("PREVENTIVO", "CORRECTIVO", "INSPECCION")
_TIPO_AUSENCIA = (
    "INCAPACIDAD", "LICENCIA_REMUNERADA", "LICENCIA_NO_REMUNERADA", "VACACIONES",
    "FALTA_INJUSTIFICADA",
)
_ORIGEN_REGISTRO = ("MANUAL", "TELEGRAM_BOT", "IMPORTACION")   # ya existe (0044): solo se referencia


def upgrade() -> None:
    for nombre, valores in (
        ("tipo_mantenimiento", _TIPO_MANTENIMIENTO),
        ("tipo_ausencia", _TIPO_AUSENCIA),
    ):
        etiquetas = ", ".join(f"'{v}'" for v in valores)
        op.execute(f"CREATE TYPE {nombre} AS ENUM ({etiquetas})")

    # asignaciones_maquina_obra — pone una máquina en una obra a un precio/mínimo pactados (pueden
    # diferir del default de la máquina). Sin timestamps: la spec no los declara para esta tabla.
    op.create_table(
        "asignaciones_maquina_obra",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("maquina_id", sa.BigInteger, sa.ForeignKey("maquinas.id"), nullable=False),
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id"), nullable=False),
        sa.Column("fecha_inicio", sa.Date, nullable=False),
        sa.Column("fecha_fin", sa.Date),
        sa.Column("precio_hora", _MONEY4, nullable=False),   # pactado para esta obra
        sa.Column("minimo_horas", sa.Integer, nullable=False),
        sa.Column("operador_id", sa.BigInteger, sa.ForeignKey("trabajadores.id")),
        sa.Column("activa", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_asignaciones_maquina_obra_obra_id", "asignaciones_maquina_obra", ["obra_id"])
    op.create_index(
        "ix_asignaciones_maquina_obra_maquina_id", "asignaciones_maquina_obra", ["maquina_id"]
    )

    # registros_horas_maquina — parte de horas por día. `horas_facturables` aplica el mínimo (Fase 3).
    op.create_table(
        "registros_horas_maquina",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("maquina_id", sa.BigInteger, sa.ForeignKey("maquinas.id"), nullable=False),
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id"), nullable=False),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("horas_trabajadas", _CANTIDAD, nullable=False),
        sa.Column("horas_facturables", _CANTIDAD, nullable=False),   # = max(trabajadas, minimo)
        sa.Column("operador_id", sa.BigInteger, sa.ForeignKey("trabajadores.id")),
        sa.Column("observaciones", sa.Text),
        sa.Column(
            "origen_registro",
            postgresql.ENUM(*_ORIGEN_REGISTRO, name="origen_registro", create_type=False),
            nullable=False, server_default="MANUAL",
        ),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_registros_horas_maquina_obra_id", "registros_horas_maquina", ["obra_id"])
    op.create_index(
        "ix_registros_horas_maquina_maquina_id", "registros_horas_maquina", ["maquina_id"]
    )
    op.create_index("ix_registros_horas_maquina_fecha", "registros_horas_maquina", ["fecha"])

    # mantenimientos — preventivo/correctivo/inspección de una máquina, con costo y próximo servicio.
    op.create_table(
        "mantenimientos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("maquina_id", sa.BigInteger, sa.ForeignKey("maquinas.id"), nullable=False),
        sa.Column(
            "tipo",
            postgresql.ENUM(*_TIPO_MANTENIMIENTO, name="tipo_mantenimiento", create_type=False),
            nullable=False,
        ),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("horas_maquina", _CANTIDAD),   # horómetro al momento del mantenimiento
        sa.Column("descripcion", sa.Text, nullable=False),
        sa.Column("costo", _MONEY4, nullable=False),
        sa.Column("proveedor_id", sa.BigInteger, sa.ForeignKey("proveedores.id")),
        sa.Column("proximo_en_horas", _CANTIDAD),   # preventivos: cada X horas
        sa.Column("proximo_en_fecha", sa.Date),
        sa.Column("factura_url", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_mantenimientos_maquina_id", "mantenimientos", ["maquina_id"])

    # asignaciones_trabajador_obra — pone un trabajador en una obra. Sin timestamps (spec).
    op.create_table(
        "asignaciones_trabajador_obra",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "trabajador_id", sa.BigInteger, sa.ForeignKey("trabajadores.id"), nullable=False
        ),
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id"), nullable=False),
        sa.Column("fecha_inicio", sa.Date, nullable=False),
        sa.Column("fecha_fin", sa.Date),
        sa.Column("activa", sa.Boolean, nullable=False, server_default=sa.text("true")),
    )
    op.create_index(
        "ix_asignaciones_trabajador_obra_obra_id", "asignaciones_trabajador_obra", ["obra_id"]
    )
    op.create_index(
        "ix_asignaciones_trabajador_obra_trabajador_id",
        "asignaciones_trabajador_obra", ["trabajador_id"],
    )

    # registros_asistencia — día de trabajo del trabajador (con HE diurnas/nocturnas/dominicales o
    # ausencia). `obra_id` NULL = día administrativo/no imputable a obra.
    op.create_table(
        "registros_asistencia",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "trabajador_id", sa.BigInteger, sa.ForeignKey("trabajadores.id"), nullable=False
        ),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id")),   # NULL = administrativo
        sa.Column("horas_trabajadas", _CANTIDAD, nullable=False, server_default="8"),
        sa.Column("horas_extra_diurnas", _CANTIDAD, nullable=False, server_default="0"),
        sa.Column("horas_extra_nocturnas", _CANTIDAD, nullable=False, server_default="0"),
        sa.Column("horas_dominical_festivo", _CANTIDAD, nullable=False, server_default="0"),
        sa.Column(
            "ausencia",
            postgresql.ENUM(*_TIPO_AUSENCIA, name="tipo_ausencia", create_type=False),
        ),
        sa.Column("observaciones", sa.Text),
        sa.Column(
            "origen_registro",
            postgresql.ENUM(*_ORIGEN_REGISTRO, name="origen_registro", create_type=False),
            nullable=False, server_default="MANUAL",
        ),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_registros_asistencia_fecha", "registros_asistencia", ["fecha"])
    op.create_index("ix_registros_asistencia_obra_id", "registros_asistencia", ["obra_id"])
    op.create_index(
        "ix_registros_asistencia_trabajador_id", "registros_asistencia", ["trabajador_id"]
    )

    # consumos_inventario — material del catálogo (`productos`) imputado a una obra. La tabla NO mueve
    # stock: el movimiento de inventario lo emite el service de Fase 3 (invariante "nada mueve stock sin
    # movimiento"). `producto_id` reusa el catálogo POS existente (spec `ItemInventario`).
    op.create_table(
        "consumos_inventario",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("producto_id", sa.BigInteger, sa.ForeignKey("productos.id"), nullable=False),
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id"), nullable=False),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("cantidad", _CANTIDAD, nullable=False),
        sa.Column("costo_unitario", _MONEY4, nullable=False),
        sa.Column("responsable", sa.Text),
        sa.Column("observaciones", sa.Text),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_consumos_inventario_obra_id", "consumos_inventario", ["obra_id"])
    op.create_index("ix_consumos_inventario_producto_id", "consumos_inventario", ["producto_id"])


def downgrade() -> None:
    # Tablas en orden inverso (drop_table lleva sus índices). Los tipos propios en orden inverso;
    # `origen_registro` NO se toca: es dueño 0044.
    op.drop_table("consumos_inventario")
    op.drop_table("registros_asistencia")
    op.drop_table("asignaciones_trabajador_obra")
    op.drop_table("mantenimientos")
    op.drop_table("registros_horas_maquina")
    op.drop_table("asignaciones_maquina_obra")
    for nombre in ("tipo_ausencia", "tipo_mantenimiento"):
        op.execute(f"DROP TYPE {nombre}")
