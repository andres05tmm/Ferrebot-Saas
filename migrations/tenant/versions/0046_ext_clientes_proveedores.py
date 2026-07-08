"""Vertical construcción — extensión backward-compatible de `clientes` y `proveedores` (plan PIM §2/§3,
spec 02 y 10): columnas NULLABLE nuevas + 2 enums.

Cuarta migración del vertical. Solo ADD COLUMN nullable (backward-compatible): las tablas ya existen
en TODAS las empresas (0001) y siguen funcionando sin tocar el POS. `clientes` gana estatus CRM y datos
de contacto/acuerdo comercial; `proveedores` gana tipo (planta de asfalto, cantera…) y contacto. Los
literales de los enums son EXACTOS a la spec. Las columnas van AL FINAL y nullable: ningún flujo
existente las requiere; el vertical construcción las llena. `estatus` trae server_default 'PROSPECTO'
(default de la spec) → las filas actuales quedan PROSPECTO; `tipo` de proveedor queda NULL en las filas
existentes (la spec no fija default).

Revision ID: 0046_ext_clientes_proveedores
Revises: 0045_construccion_operacion
Create Date: 2026-07-06

Nota: el `revision` id se abrevia a 0046_ext_… porque `alembic_version.version_num` es VARCHAR(32) y
el nombre largo (35) no cabe; el archivo conserva el nombre descriptivo.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0046_ext_clientes_proveedores"
down_revision: str | None = "0045_construccion_operacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enums (literales EXACTOS a la spec del cliente: 02 EstatusCliente, 10 TipoProveedor).
_ESTATUS_CLIENTE = ("PROSPECTO", "ACTIVO", "RECURRENTE", "INACTIVO", "MOROSO")
_TIPO_PROVEEDOR = (
    "PLANTA_ASFALTO", "CANTERA_ARENA", "REPUESTOS", "COMBUSTIBLE", "TRANSPORTE", "SERVICIOS", "OTRO",
)


def upgrade() -> None:
    for nombre, valores in (
        ("estatus_cliente", _ESTATUS_CLIENTE),
        ("tipo_proveedor", _TIPO_PROVEEDOR),
    ):
        etiquetas = ", ".join(f"'{v}'" for v in valores)
        op.execute(f"CREATE TYPE {nombre} AS ENUM ({etiquetas})")

    # clientes — CRM: estatus + contacto + acuerdo comercial (spec 02). Todas nullable.
    op.add_column(
        "clientes",
        sa.Column(
            "estatus",
            postgresql.ENUM(*_ESTATUS_CLIENTE, name="estatus_cliente", create_type=False),
            server_default="PROSPECTO",   # default de la spec; las filas actuales quedan PROSPECTO
        ),
    )
    op.add_column("clientes", sa.Column("contacto_nombre", sa.Text))
    op.add_column("clientes", sa.Column("contacto_cargo", sa.Text))
    op.add_column("clientes", sa.Column("contacto_telefono", sa.Text))
    op.add_column("clientes", sa.Column("contacto_email", sa.Text))
    op.add_column("clientes", sa.Column("acuerdo_comercial", sa.Text))   # condiciones de pago/descuentos

    # proveedores — tipo (para análisis de precios por rubro) + contacto (spec 10). Todas nullable.
    op.add_column(
        "proveedores",
        sa.Column(
            "tipo",
            postgresql.ENUM(*_TIPO_PROVEEDOR, name="tipo_proveedor", create_type=False),
        ),
    )
    op.add_column("proveedores", sa.Column("contacto_nombre", sa.Text))
    op.add_column("proveedores", sa.Column("contacto_telefono", sa.Text))
    op.add_column("proveedores", sa.Column("contacto_email", sa.Text))


def downgrade() -> None:
    # Columnas en orden inverso, LUEGO los tipos (las columnas dependen del tipo).
    op.drop_column("proveedores", "contacto_email")
    op.drop_column("proveedores", "contacto_telefono")
    op.drop_column("proveedores", "contacto_nombre")
    op.drop_column("proveedores", "tipo")

    op.drop_column("clientes", "acuerdo_comercial")
    op.drop_column("clientes", "contacto_email")
    op.drop_column("clientes", "contacto_telefono")
    op.drop_column("clientes", "contacto_cargo")
    op.drop_column("clientes", "contacto_nombre")
    op.drop_column("clientes", "estatus")

    for nombre in ("tipo_proveedor", "estatus_cliente"):
        op.execute(f"DROP TYPE {nombre}")
