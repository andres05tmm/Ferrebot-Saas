"""Fase 7 (DIAN) — trazabilidad obra→documento fiscal + máquina de estados de transmisión de nómina
electrónica (CUNE), espejando el patrón `fe_estado` de la facturación.

Décima migración del vertical construcción (Fase 7, sobre 0049). Backward-compatible: SOLO
ADD COLUMN nullable/CON default + CREATE TYPE + índices. NO toca datos existentes. Se aplica vacía al
resto de empresas vía `tools.migrate_tenants` (columnas NULL / default PENDIENTE no pesan; el acceso lo
gatea la flag `nomina_electronica`). La transmisión REAL a DIAN queda GO-LIVE GATED (habilitación
Software Propio + certificado + resolución + cuenta MATIAS real de PIM): esta migración solo habilita el
esquema del PIPELINE probado contra el mock MATIAS (`MATIAS_AMBIENTE=pruebas`).

Piezas:
  - ALTER `facturas_electronicas` ADD `obra_id` (BigInteger NULL, FK → obras.id, SIN ondelete) + índice
    `ix_facturas_electronicas_obra_id`. RASTRO puro obra→documento (spec 15 §1 "From /obras/[id]
    Invoice"): la EMISIÓN sigue montándose sobre `venta_id` (reuso 100% de `FacturacionService`); esta
    columna solo liga el documento a la obra que lo originó, para la vista "facturas de esta obra". Sin
    `ondelete`: las obras usan soft delete (`eliminado_en`) y, como `liquidaciones_obra`/`cargos_alquiler`,
    el rastro fiscal nunca se borra en cascada (invariante "histórico fiscal no se borra"). NO se agrega
    `cotizacion_obra_id`: la obra es 1-1 con su cotización (`obras.cotizacion_id`), así que la cotización
    ya es alcanzable transitivamente desde la factura; una segunda FK sería peso muerto en todo tenant.
  - CREATE TYPE `estado_transmision_nomina` ('PENDIENTE','TRANSMITIDO','RECHAZADO','ERROR') + ALTER
    `detalles_liquidacion` ADD `estado_transmision` (NOT NULL default 'PENDIENTE'), `intentos_transmision`
    (SmallInteger NOT NULL default 0) y `transmision_respuesta` (JSONB NULL). Completa la idempotencia de
    la nómina electrónica: `cune_dian`/`fecha_transmision_dian` (ya de 0047) daban el ancla mínima
    (NULL=no transmitido, set=transmitido), pero no distinguían "nunca intentado" de "RECHAZADO por DIAN"
    de "ERROR técnico transitorio". La máquina de estados espeja el operativo de `fe_estado`
    (pendiente→aceptada|rechazada|error): TRANSMITIDO≈aceptada (idempotencia dura: una vez TRANSMITIDO no
    se re-transmite), RECHAZADO≈rechazada (terminal de negocio, sin auto-reintento), ERROR≈error
    (transitorio/5xx, reintentable con backoff). Sin la separación ERROR↔RECHAZADO el pipeline ARQ
    conflaría un 5xx pasajero con un rechazo real (auto-reintentaría un rechazo o se rendiría ante un
    blip), justo lo que `_resultado_5xx`/`decidir_emision` evitan en FE. `intentos_transmision` acota el
    dead-letter (paridad con `facturas_electronicas.intentos` + MAX_INTENTOS); `transmision_respuesta`
    guarda la respuesta MATIAS completa (motivo de rechazo legible + histórico fiscal/audit, paridad con
    `dian_respuesta`). La transmisión es PER `DetalleLiquidacion` DIRECTO (spec 08: solo directos obtienen
    CUNE; los PATACALIENTE no generan documento) → el estado vive en el detalle, no en `PeriodoNomina`
    (cuyo `estado` ABIERTO/LIQUIDADO/PAGADO es el ciclo de LIQUIDACIÓN, no el de transmisión DIAN; no se
    tocó para no conflar dos concerns). El pipeline debe filtrar por `tipo_vinculacion='DIRECTO'` al barrer
    transmitibles: los patacalientes quedan PENDIENTE de por vida pero excluidos por ese filtro (mismo
    criterio que FE, que solo recoge las filas que le tocan).

Dinero N/A aquí. `obra_id` en BigInteger (patrón del repo: FK en la migración, ORM sin `relationship`).

Revision ID: 0050_fe_obra_nomina_cune
Revises: 0049_cartera_alquiler_consumo
Create Date: 2026-07-07

Nota: el `revision` id (`0050_fe_obra_nomina_cune`, 24 chars) cabe en `alembic_version.version_num`
(VARCHAR(32)).

Salvedad "histórico fiscal no se borra" (regla no negociable #): el `downgrade` es simétrico y de USO
DEV (base efímera): dropea las columnas nuevas —que en la Ola A están vacías (ningún CUNE emitido)— y el
tipo enum. En PRODUCCIÓN no se hace downgrade una vez pobladas: el CUNE/respuesta de una nómina
transmitida es documento fiscal (retención ~5 años) y no se revierte. En `obra_id` el downgrade retira
SOLO el puntero de rastro (la fila `facturas_electronicas` —el documento fiscal— sobrevive intacta).
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_fe_obra_nomina_cune"
down_revision: str | None = "0049_cartera_alquiler_consumo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Máquina de estados de transmisión de nómina electrónica (espejo operativo de `fe_estado`, literales
# EXACTOS que consume el pipeline ARQ y el ORM `DetalleLiquidacion`).
_ESTADO_TRANSMISION = ("PENDIENTE", "TRANSMITIDO", "RECHAZADO", "ERROR")


def upgrade() -> None:
    # --- A) RASTRO obra→documento fiscal (facturar desde obra reusa la emisión sobre venta_id) ---------
    op.add_column(
        "facturas_electronicas",
        # SIN ondelete: la obra usa soft delete; el rastro fiscal nunca cae en cascada.
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id")),
    )
    op.create_index(
        "ix_facturas_electronicas_obra_id", "facturas_electronicas", ["obra_id"]
    )

    # --- B) Nómina electrónica: máquina de estados de transmisión (CUNE) sobre detalles_liquidacion ----
    etiquetas = ", ".join(f"'{v}'" for v in _ESTADO_TRANSMISION)
    op.execute(f"CREATE TYPE estado_transmision_nomina AS ENUM ({etiquetas})")

    op.add_column(
        "detalles_liquidacion",
        sa.Column(
            "estado_transmision",
            postgresql.ENUM(
                *_ESTADO_TRANSMISION, name="estado_transmision_nomina", create_type=False
            ),
            nullable=False,
            server_default="PENDIENTE",   # rellena las filas ya liquidadas (Ola A): nunca transmitidas
        ),
    )
    op.add_column(
        "detalles_liquidacion",
        # Acota el dead-letter del pipeline ARQ (paridad con facturas_electronicas.intentos).
        sa.Column("intentos_transmision", sa.SmallInteger, nullable=False, server_default="0"),
    )
    op.add_column(
        "detalles_liquidacion",
        # Respuesta MATIAS completa (motivo de rechazo legible + histórico fiscal/audit). Paridad con
        # facturas_electronicas.dian_respuesta.
        sa.Column("transmision_respuesta", postgresql.JSONB),
    )


def downgrade() -> None:
    # Simétrico y de uso DEV (ver salvedad "histórico fiscal no se borra" en el docstring). Orden inverso.
    op.drop_column("detalles_liquidacion", "transmision_respuesta")
    op.drop_column("detalles_liquidacion", "intentos_transmision")
    op.drop_column("detalles_liquidacion", "estado_transmision")
    op.execute("DROP TYPE estado_transmision_nomina")

    op.drop_index("ix_facturas_electronicas_obra_id", table_name="facturas_electronicas")
    op.drop_column("facturas_electronicas", "obra_id")
