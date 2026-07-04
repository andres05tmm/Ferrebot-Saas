"""Reconciliación DDL de 7 tablas ahora mapeadas por ORM (ADR 0025).

`notas_electronicas`, `documentos_soporte`, `eventos_dian`, `iva_saldos_bimestrales`, `libro_iva`,
`cuentas_cobro` y `bancolombia_transferencias` existían desde la migración 0001 pero SIN modelo
SQLAlchemy. La Fase 2 (ADR 0025) crea esos modelos; esta migración es la red de seguridad que
garantiza que la DB y la metadata ORM queden alineadas en cualquier entorno.

Idempotente por diseño: `CREATE TABLE IF NOT EXISTS` con DDL que ESPEJA la 0001. En una base ya
migrada (el caso normal) es un no-op total. El `downgrade` es intencionalmente vacío: estas tablas
pertenecen a la 0001, no a esta migración; revertir la 0030 no debe borrarlas.

Revision ID: 0030_orm_huerfanas
Revises: 0029_mov_fecha_operacion
Create Date: 2026-07-03
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0030_orm_huerfanas"
down_revision: str | None = "0029_mov_fecha_operacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# DDL espejo de la 0001 (tipos: MONEY = NUMERIC(12,2); enums fe_tipo/fe_estado ya existen). Orden con
# las dependencias de FK primero (cuentas_cobro antes de documentos_soporte).
_TABLAS = (
    """
    CREATE TABLE IF NOT EXISTS cuentas_cobro (
        id BIGSERIAL PRIMARY KEY,
        consecutivo BIGINT,
        numero_display TEXT,
        periodo TEXT,
        concepto TEXT,
        valor NUMERIC(12,2),
        cliente_id BIGINT REFERENCES clientes(id),
        enviado_telegram BOOLEAN DEFAULT false,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notas_electronicas (
        id BIGSERIAL PRIMARY KEY,
        factura_id BIGINT REFERENCES facturas_electronicas(id),
        tipo fe_tipo NOT NULL,
        motivo TEXT,
        cufe TEXT,
        estado fe_estado NOT NULL DEFAULT 'pendiente',
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS documentos_soporte (
        id BIGSERIAL PRIMARY KEY,
        consecutivo TEXT,
        fecha DATE,
        valor NUMERIC(12,2),
        cude TEXT,
        estado_dian TEXT,
        cuenta_cobro_id BIGINT REFERENCES cuentas_cobro(id),
        idempotency_key TEXT UNIQUE,
        intentos SMALLINT NOT NULL DEFAULT 0,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
        emitido_en TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS eventos_dian (
        id BIGSERIAL PRIMARY KEY,
        factura_id BIGINT REFERENCES facturas_electronicas(id),
        evento TEXT,
        estado TEXT,
        payload JSONB,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS iva_saldos_bimestrales (
        id BIGSERIAL PRIMARY KEY,
        anio INTEGER,
        bimestre SMALLINT,
        iva_generado NUMERIC(12,2),
        iva_descontable NUMERIC(12,2),
        saldo NUMERIC(12,2),
        CONSTRAINT uq_iva_saldos_periodo UNIQUE (anio, bimestre)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS libro_iva (
        id BIGSERIAL PRIMARY KEY,
        fecha DATE,
        tipo TEXT,
        base NUMERIC(12,2),
        iva NUMERIC(12,2),
        referencia TEXT,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bancolombia_transferencias (
        id BIGSERIAL PRIMARY KEY,
        gmail_message_id TEXT NOT NULL UNIQUE,
        fecha DATE NOT NULL,
        hora TEXT,
        monto NUMERIC(12,2) NOT NULL,
        remitente TEXT,
        descripcion TEXT,
        tipo_transaccion TEXT,
        referencia TEXT,
        notificado BOOLEAN NOT NULL DEFAULT true,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
)


def upgrade() -> None:
    for ddl in _TABLAS:
        op.execute(ddl)


def downgrade() -> None:
    # No-op deliberado: las tablas pertenecen a la 0001 (esta migración solo reconcilia). Borrarlas
    # aquí destruiría datos fiscales/bancarios legítimos. El downgrade queda limpio sin tocarlas.
    pass
