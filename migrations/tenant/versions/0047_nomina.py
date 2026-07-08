"""Vertical construcción — nómina: periodos, liquidación por trabajador y prorrateo a obra (grupo 4 del
plan PIM §3, spec cliente 08_MODULO_NOMINA).

Única migración de la Ola A (contrato `docs/research/pim-olaA-contrato.md` §1): 0047 es de la Fase 4 y
de nadie más. Backward-compatible: CREATE de 3 tablas nuevas + ADD COLUMN sobre `parametros_legales`
(las 4 columnas que el motor de nómina necesita y que 0043 aún no tenía). Se aplica a TODAS las empresas
vía `tools.migrate_tenants`; las tablas viven vacías donde no se usa el vertical (tabla vacía no pesa).

Piezas:
  - ALTER `parametros_legales` ADD `horas_mes`, `recargo_he_diurna/nocturna`, `recargo_dominical`: los
    campos que espera el snapshot `services.calculations.nomina.ParametrosNomina` y que la tabla no
    traía. Se agregan NOT NULL con server_default PROVISIONAL (240 h/mes; recargos 1.25/1.75/2.0), en
    la misma línea que los parafiscales que ya traían default en 0043. Valores reales [DEFINIR contador]
    (errores acá tienen implicación legal): se corrige la parametrización, el motor no se toca.
  - `periodos_nomina`: rango de fechas + estado (ABIERTO→LIQUIDADO→PAGADO) + SNAPSHOT congelado de los
    parámetros legales al crear el periodo (columnas `param_*`). El snapshot es el invariante de la spec
    ("On period creation, freeze the valid `ParametrosLegales` row"): aunque luego cambie la
    parametrización vigente, la liquidación del periodo usa los valores del día que se creó.
  - `detalles_liquidacion`: la liquidación por trabajador (devengados/deducciones/aportes/provisiones/
    neto). UNIQUE(periodo_id, trabajador_id) = idempotencia de re-liquidar (un detalle por trabajador).
    `cune_dian`/`fecha_transmision_dian` nullable = nómina electrónica (Fase 7), aquí siempre NULL.
  - `prorrateo_nomina_obra`: reparte el costo total del trabajador entre obras según días trabajados
    (obra_id NULL = administrativo). Alimenta el gasto real de obra (Fase 3). Índice por `obra_id`.

Dinero en NUMERIC(18,4) (MONEY4, divergencia documentada en core/money.py); porcentajes en NUMERIC(6,4);
horas/días en NUMERIC(18,4); `horas_mes` en NUMERIC(6,2). Los tipos enum propios se crean aquí;
`tipo_vinculacion` se REUSA (dueño 0043, create_type=False) y su downgrade NO lo dropea.

Revision ID: 0047_nomina_liquidacion
Revises: 0046_ext_clientes_proveedores
Create Date: 2026-07-06

Nota: el `revision` id se abrevia a `0047_nomina_liquidacion` (23) porque `alembic_version.version_num`
es VARCHAR(32); el archivo conserva el nombre corto `0047_nomina.py`.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0047_nomina_liquidacion"
down_revision: str | None = "0046_ext_clientes_proveedores"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY4 = sa.Numeric(18, 4)     # dinero del vertical construcción (spec @db.Decimal(18,4))
_PCT = sa.Numeric(6, 4)         # porcentaje/recargo como fracción o multiplicador con 4 decimales
_CANTIDAD = sa.Numeric(18, 4)   # días/horas imputadas (misma precisión que la spec)
_HORAS_MES = sa.Numeric(6, 2)   # convención de horas/mes (240.00)

# Enums nuevos de esta migración (literales EXACTOS a la semántica de la spec 08). `tipo_vinculacion`
# NO se crea aquí: es dueño 0043 y se referencia con create_type=False.
_ESTADO_PERIODO = ("ABIERTO", "LIQUIDADO", "PAGADO")
_TIPO_PERIODO = ("QUINCENAL", "MENSUAL", "SEMANAL")
_TIPO_VINCULACION = ("DIRECTO", "PATACALIENTE")   # ya existe (0043): solo se referencia


def upgrade() -> None:
    for nombre, valores in (
        ("estado_periodo_nomina", _ESTADO_PERIODO),
        ("tipo_periodo_nomina", _TIPO_PERIODO),
    ):
        etiquetas = ", ".join(f"'{v}'" for v in valores)
        op.execute(f"CREATE TYPE {nombre} AS ENUM ({etiquetas})")

    # --- ALTER parametros_legales: las 4 columnas que faltaban para el snapshot del motor -----------
    # NOT NULL con server_default PROVISIONAL [DEFINIR contador]: rellena las filas ya sembradas por el
    # loader del pack y garantiza que todo periodo congele un snapshot completo. Mecánica fija; valores
    # tentativos (recargos 1.25/1.75/2.0; 240 h/mes, spec 08).
    op.add_column(
        "parametros_legales",
        sa.Column("horas_mes", _HORAS_MES, nullable=False, server_default="240"),
    )
    op.add_column(
        "parametros_legales",
        sa.Column("recargo_he_diurna", _PCT, nullable=False, server_default="1.25"),
    )
    op.add_column(
        "parametros_legales",
        sa.Column("recargo_he_nocturna", _PCT, nullable=False, server_default="1.75"),
    )
    op.add_column(
        "parametros_legales",
        sa.Column("recargo_dominical", _PCT, nullable=False, server_default="2.0"),
    )

    # --- periodos_nomina: rango + estado + snapshot congelado de parametros_legales ----------------
    op.create_table(
        "periodos_nomina",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text),                       # etiqueta libre ("Quincena 1 jul 2026")
        sa.Column(
            "tipo",
            postgresql.ENUM(*_TIPO_PERIODO, name="tipo_periodo_nomina", create_type=False),
            nullable=False, server_default="QUINCENAL",
        ),
        sa.Column("fecha_inicio", sa.Date, nullable=False),
        sa.Column("fecha_fin", sa.Date, nullable=False),
        sa.Column(
            "estado",
            postgresql.ENUM(*_ESTADO_PERIODO, name="estado_periodo_nomina", create_type=False),
            nullable=False, server_default="ABIERTO",
        ),
        # Traza a la fila de parametros_legales usada (SET NULL si esa fila se borra; el snapshot vive
        # en las columnas param_*, así que el valor congelado NO depende de esta FK).
        sa.Column(
            "parametros_legales_id",
            sa.BigInteger,
            sa.ForeignKey("parametros_legales.id", ondelete="SET NULL"),
        ),
        # Snapshot congelado (todas las columnas del ParametrosNomina que consume el motor). NOT NULL:
        # un periodo siempre nace con snapshot completo.
        sa.Column("param_smmlv", _MONEY4, nullable=False),
        sa.Column("param_auxilio_transporte", _MONEY4, nullable=False),
        sa.Column("param_auxilio_transporte_tope_smmlv", sa.Integer, nullable=False),
        sa.Column("param_horas_mes", _HORAS_MES, nullable=False),
        sa.Column("param_recargo_he_diurna", _PCT, nullable=False),
        sa.Column("param_recargo_he_nocturna", _PCT, nullable=False),
        sa.Column("param_recargo_dominical", _PCT, nullable=False),
        sa.Column("param_salud_empleado_pct", _PCT, nullable=False),
        sa.Column("param_pension_empleado_pct", _PCT, nullable=False),
        sa.Column("param_salud_empleador_pct", _PCT, nullable=False),
        sa.Column("param_pension_empleador_pct", _PCT, nullable=False),
        sa.Column("param_arl_pct", _PCT, nullable=False),
        sa.Column("param_caja_compensacion_pct", _PCT, nullable=False),
        sa.Column("param_sena_pct", _PCT, nullable=False),
        sa.Column("param_icbf_pct", _PCT, nullable=False),
        sa.Column("param_cesantias_pct", _PCT, nullable=False),
        sa.Column("param_intereses_cesantias_pct", _PCT, nullable=False),
        sa.Column("param_prima_pct", _PCT, nullable=False),
        sa.Column("param_vacaciones_pct", _PCT, nullable=False),
        sa.Column("liquidado_en", sa.TIMESTAMP(timezone=True)),
        sa.Column("pagado_en", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_periodos_nomina_estado", "periodos_nomina", ["estado"])

    # --- detalles_liquidacion: liquidación por trabajador (espeja services...nomina.Liquidacion) ---
    op.create_table(
        "detalles_liquidacion",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "periodo_id",
            sa.BigInteger,
            sa.ForeignKey("periodos_nomina.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "trabajador_id", sa.BigInteger, sa.ForeignKey("trabajadores.id"), nullable=False
        ),
        # Cómo se liquidó (snapshot del vínculo al momento de liquidar).
        sa.Column(
            "tipo_vinculacion",
            postgresql.ENUM(*_TIPO_VINCULACION, name="tipo_vinculacion", create_type=False),
            nullable=False,
        ),
        sa.Column("dias_liquidados", _CANTIDAD, nullable=False, server_default="0"),
        # Devengados.
        sa.Column("salario_devengado", _MONEY4, nullable=False),
        sa.Column("auxilio_transporte", _MONEY4, nullable=False),
        sa.Column("valor_horas_extra", _MONEY4, nullable=False),
        sa.Column("total_devengado", _MONEY4, nullable=False),
        # Deducciones (empleado).
        sa.Column("salud_empleado", _MONEY4, nullable=False),
        sa.Column("pension_empleado", _MONEY4, nullable=False),
        sa.Column("total_deducciones", _MONEY4, nullable=False),
        # Neto a pagar.
        sa.Column("neto_pagar", _MONEY4, nullable=False),
        # Aportes empleador + provisiones (costeo real, no van al neto).
        sa.Column("aportes_empleador", _MONEY4, nullable=False),
        sa.Column("provisiones", _MONEY4, nullable=False),
        # Nómina electrónica (Fase 7): siempre NULL en la Ola A.
        sa.Column("cune_dian", sa.Text),
        sa.Column("fecha_transmision_dian", sa.TIMESTAMP(timezone=True)),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "actualizado_en", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        # Un detalle por trabajador y periodo: ancla la idempotencia de re-liquidar (UPSERT).
        sa.UniqueConstraint(
            "periodo_id", "trabajador_id", name="uq_detalle_liquidacion_periodo_trabajador"
        ),
    )
    op.create_index("ix_detalles_liquidacion_trabajador_id", "detalles_liquidacion", ["trabajador_id"])

    # --- prorrateo_nomina_obra: reparte el costo total del trabajador entre obras por días ---------
    op.create_table(
        "prorrateo_nomina_obra",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "periodo_id",
            sa.BigInteger,
            sa.ForeignKey("periodos_nomina.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "trabajador_id", sa.BigInteger, sa.ForeignKey("trabajadores.id"), nullable=False
        ),
        # NULL = nómina administrativa (días no imputables a una obra concreta).
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id")),
        sa.Column("dias_imputados", _CANTIDAD, nullable=False),
        sa.Column("costo_imputado", _MONEY4, nullable=False),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_prorrateo_nomina_obra_periodo_id", "prorrateo_nomina_obra", ["periodo_id"])
    op.create_index("ix_prorrateo_nomina_obra_obra_id", "prorrateo_nomina_obra", ["obra_id"])
    op.create_index(
        "ix_prorrateo_nomina_obra_trabajador_id", "prorrateo_nomina_obra", ["trabajador_id"]
    )


def downgrade() -> None:
    # Tablas en orden inverso (drop_table lleva sus índices), luego las columnas de parametros_legales
    # y por último los tipos enum PROPIOS. `tipo_vinculacion` NO se toca (dueño 0043).
    op.drop_table("prorrateo_nomina_obra")
    op.drop_table("detalles_liquidacion")
    op.drop_table("periodos_nomina")

    op.drop_column("parametros_legales", "recargo_dominical")
    op.drop_column("parametros_legales", "recargo_he_nocturna")
    op.drop_column("parametros_legales", "recargo_he_diurna")
    op.drop_column("parametros_legales", "horas_mes")

    for nombre in ("tipo_periodo_nomina", "estado_periodo_nomina"):
        op.execute(f"DROP TYPE {nombre}")
