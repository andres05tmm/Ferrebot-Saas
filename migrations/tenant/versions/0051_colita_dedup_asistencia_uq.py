"""Polish del vertical construcción (Stream C): dedup del aviso de colitas de alquiler + clave natural
única de la asistencia diaria.

Undécima migración del vertical construcción (sobre 0050). Backward-compatible: solo ADD COLUMN nullable
+ CREATE UNIQUE INDEX (previa deduplicación de las filas ya existentes). NO crea tablas ni enums nuevos
(el guard `tests/test_schema_paridad.py` no cambia; `tests/test_migrations.py` sigue con 42 enums). Se
aplica vacía al resto de empresas vía `tools.migrate_tenants` (columna NULL / índice sobre tablas hoy
vacías no pesan; el acceso lo gatea la flag `cartera_alquiler`/`nomina`).

Piezas:
  - ALTER `obras` ADD `ultimo_aviso_colita_en` (TIMESTAMPTZ NULL). Estado de DEDUP del cron
    `detectar_colitas_alquiler` (worker): sella CUÁNDO se avisó por última vez al dueño de la colita de
    esta obra, para respetar `cartera_config.cadencia_aviso_dias` y NO re-avisar todos los días (MEDIUM-1).
    Se prefiere una COLUMNA sobre una tabla-dedup nueva (al estilo de `pagar_avisos.ultimo_aviso_en`): la
    colita es por (cliente, obra) y una obra es 1-1 con su cliente, así que `obras` es el ancla natural de
    una sola fila por (cliente, obra) —y evita tocar el guard de paridad—. La columna es un concern de
    cartera físicamente montado sobre `obras`; la escribe SOLO el repo de cartera (UPDATE acotado), la
    obra no la consume. NULL = nunca avisada; el cron la lee y la sella en la MISMA transacción del NOTIFY.
  - CREATE UNIQUE INDEX `uq_registros_asistencia_trabajador_fecha` ON `registros_asistencia`
    (trabajador_id, fecha). Clave NATURAL de un día de trabajo (spec `RegistroAsistencia`): un trabajador
    tiene UN registro por día. Sin ella, dos altas del mismo día inflan `dias_trabajados` (cada registro
    suma +1 en `_agregar_asistencia`), inflando salario proporcional y prorrateo a obra. El
    `registrar_asistencia` del repo pasa a UPSERT por esta clave (idempotente: re-registrar el día
    ACTUALIZA la fila, no duplica). Antes de crear el índice se DEDUPLICAN las filas existentes
    conservando la más reciente (MAX(id)) por (trabajador_id, fecha) —seguro aunque la tabla esté vacía—.

Dinero N/A aquí.

Revision ID: 0051_colita_dedup_asistencia_uq
Revises: 0050_fe_obra_nomina_cune
Create Date: 2026-07-07

Nota: el `revision` id (`0051_colita_dedup_asistencia_uq`, 31 chars) cabe en
`alembic_version.version_num` (VARCHAR(32)).

Salvedad de `downgrade` (uso DEV, base efímera): simétrico —retira el índice y la columna—. La columna de
dedup no es dato fiscal; en dev está vacía o irrelevante. La deduplicación del `upgrade` NO se revierte
(no se pueden resucitar filas borradas), pero es un no-op sobre datos limpios; en prod la tabla del
vertical arranca vacía.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_colita_dedup_asistencia_uq"
down_revision: str | None = "0050_fe_obra_nomina_cune"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- A) DEDUP del aviso de colitas: estado "último aviso" por (cliente, obra) sobre `obras` ----------
    # Columna nullable (patrón `pagar_avisos.ultimo_aviso_en`): el cron la lee y la sella respetando
    # `cartera_config.cadencia_aviso_dias`. NULL = nunca avisada.
    op.add_column("obras", sa.Column("ultimo_aviso_colita_en", sa.TIMESTAMP(timezone=True)))

    # --- B) Clave natural única de la asistencia diaria (un registro por trabajador y día) --------------
    # Deduplica primero (conserva la fila más reciente por (trabajador_id, fecha)); seguro aunque la tabla
    # esté vacía. Luego el índice único ancla la idempotencia del UPSERT del repo.
    op.execute(
        "DELETE FROM registros_asistencia a "
        "USING registros_asistencia b "
        "WHERE a.trabajador_id = b.trabajador_id "
        "  AND a.fecha = b.fecha "
        "  AND a.id < b.id"
    )
    op.create_index(
        "uq_registros_asistencia_trabajador_fecha",
        "registros_asistencia",
        ["trabajador_id", "fecha"],
        unique=True,
    )


def downgrade() -> None:
    # Simétrico y de uso DEV (ver salvedad del docstring). Orden inverso. La deduplicación no se revierte.
    op.drop_index(
        "uq_registros_asistencia_trabajador_fecha", table_name="registros_asistencia"
    )
    op.drop_column("obras", "ultimo_aviso_colita_en")
