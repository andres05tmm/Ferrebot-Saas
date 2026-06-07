"""Pack Agenda/Citas — tablas de negocio del primer pack de acción (docs/pack-agenda-citas.md).

Crea la capa de datos del pack en el árbol TENANT (son datos de negocio, no control): tipos enum,
config que nutre el negocio (`servicios`, `recursos`, `recurso_servicio`, `disponibilidad`,
`bloqueos`, `agenda_config`) y la transaccional `citas`. DDL a mano (materializa el doc), igual que
el resto del esquema. Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

`recurso.tipo` es genérico (profesional/sala/equipo/mesa/cancha). `agenda_config` es de fila única
(CHECK id = 1). `anticipo_tipo`/`anticipo_valor` van nullable: el cobro real se cablea con el frente
de pagos. `citas.estado` es enum; `idempotency_key` única (idempotencia, regla no negociable #8).

Downgrade: dropea tablas (orden inverso por las FKs) y luego los tipos enum.

Revision ID: 0008_agenda_citas
Revises: 0007_metodo_pago_datafono
Create Date: 2026-06-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_agenda_citas"
down_revision: str | None = "0007_metodo_pago_datafono"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS = {
    "recurso_tipo": ("profesional", "sala", "equipo", "mesa", "cancha"),
    "cita_estado": ("pendiente", "confirmada", "cumplida", "cancelada", "no_show"),
    "cita_origen": ("whatsapp", "dashboard"),
    "modo_confirmacion": ("auto", "manual"),
    "anticipo_tipo": ("porcentaje", "fijo"),
}

MONEY = sa.Numeric(12, 2)

# Tablas en orden inverso de creación para el downgrade (respeta las FKs).
_TABLAS = (
    "citas", "agenda_config", "bloqueos", "disponibilidad",
    "recurso_servicio", "recursos", "servicios",
)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def _ts(col: str = "creado_en", nullable: bool = False) -> sa.Column:
    return sa.Column(col, sa.TIMESTAMP(timezone=True), nullable=nullable, server_default=sa.text("now()"))


def upgrade() -> None:
    for name, values in _ENUMS.items():
        valores = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({valores})")

    op.create_table(
        "servicios",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("duracion_min", sa.Integer, nullable=False),
        sa.Column("precio", MONEY),
        sa.Column("buffer_antes_min", sa.Integer, nullable=False, server_default="0"),
        sa.Column("buffer_despues_min", sa.Integer, nullable=False, server_default="0"),
        sa.Column("categoria", sa.Text),
        sa.Column("descripcion", sa.Text),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts(),
    )

    op.create_table(
        "recursos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("tipo", _enum("recurso_tipo"), nullable=False),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts(),
    )

    op.create_table(
        "recurso_servicio",
        sa.Column("recurso_id", sa.BigInteger, sa.ForeignKey("recursos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("servicio_id", sa.BigInteger, sa.ForeignKey("servicios.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "disponibilidad",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("recurso_id", sa.BigInteger, sa.ForeignKey("recursos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dia_semana", sa.SmallInteger, nullable=False),  # 0=lunes … 6=domingo
        sa.Column("hora_inicio", sa.Time, nullable=False),
        sa.Column("hora_fin", sa.Time, nullable=False),
        sa.CheckConstraint("dia_semana BETWEEN 0 AND 6", name="ck_disponibilidad_dia_semana"),
    )
    op.create_index("ix_disponibilidad_recurso_dia", "disponibilidad", ["recurso_id", "dia_semana"])

    op.create_table(
        "bloqueos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # recurso_id NULL = bloqueo global del negocio.
        sa.Column("recurso_id", sa.BigInteger, sa.ForeignKey("recursos.id", ondelete="CASCADE")),
        sa.Column("inicio", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("fin", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("motivo", sa.Text),
        _ts(),
    )
    op.create_index("ix_bloqueos_recurso_inicio", "bloqueos", ["recurso_id", "inicio"])

    op.create_table(
        "agenda_config",
        sa.Column("id", sa.SmallInteger, primary_key=True, server_default="1"),
        sa.Column("zona_horaria", sa.Text, nullable=False, server_default="America/Bogota"),
        sa.Column("intervalo_slots_min", sa.Integer, nullable=False, server_default="15"),
        sa.Column("anticipacion_minima_min", sa.Integer, nullable=False, server_default="120"),
        sa.Column("ventana_maxima_dias", sa.Integer, nullable=False, server_default="30"),
        sa.Column("politica_cancelacion_horas", sa.Integer, nullable=False, server_default="24"),
        sa.Column("permite_reagendar", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("modo_confirmacion", _enum("modo_confirmacion"), nullable=False, server_default="auto"),
        sa.Column("requiere_anticipo", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("anticipo_tipo", _enum("anticipo_tipo")),   # nullable: cobro futuro
        sa.Column("anticipo_valor", MONEY),                    # nullable: cobro futuro
        sa.Column("capacidad_por_slot", sa.Integer, nullable=False, server_default="1"),
        sa.Column("recordatorios_horas", postgresql.ARRAY(sa.Integer), nullable=False, server_default="{24,2}"),
        sa.Column("persona", sa.Text),
        _ts(),
        _ts("actualizado_en", nullable=True),
        # Fila única por tenant.
        sa.CheckConstraint("id = 1", name="ck_agenda_config_fila_unica"),
    )

    op.create_table(
        "citas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("servicio_id", sa.BigInteger, sa.ForeignKey("servicios.id"), nullable=False),
        sa.Column("recurso_id", sa.BigInteger, sa.ForeignKey("recursos.id"), nullable=False),
        sa.Column("cliente_nombre", sa.Text, nullable=False),
        sa.Column("cliente_telefono", sa.Text, nullable=False),
        sa.Column("inicio", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("fin", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("estado", _enum("cita_estado"), nullable=False, server_default="pendiente"),
        sa.Column("origen", _enum("cita_origen"), nullable=False, server_default="whatsapp"),
        sa.Column("notas", sa.Text),
        sa.Column("idempotency_key", sa.Text, unique=True),
        _ts("creada_en"),
    )
    # Disponibilidad: citas de un recurso en una ventana. mis_citas: por teléfono del cliente.
    op.create_index("ix_citas_recurso_inicio", "citas", ["recurso_id", "inicio"])
    op.create_index("ix_citas_cliente_telefono", "citas", ["cliente_telefono"])


def downgrade() -> None:
    for table in _TABLAS:
        op.drop_table(table)
    for name in _ENUMS:
        op.execute(f"DROP TYPE {name}")
