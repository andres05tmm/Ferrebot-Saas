"""Vertical construcción — imputación a obra en gastos/compras + snapshot de liquidación (plan PIM §2/§3,
spec cliente 09_MODULO_GASTOS_CAJA_MENOR y 11_MODULO_COMPRAS_MARGENES, más el cierre de obra de 04).

Quinta migración del vertical (Fase 3). Backward-compatible: solo ADD COLUMN nullable sobre `gastos` y
`compras` (tablas del POS que ya existen en TODAS las empresas desde 0001) + CREATE de una tabla nueva
(`liquidaciones_obra`). No reescribe nada del POS; las columnas nuevas viven NULL en las filas actuales
y las llena el vertical construcción. Se aplica a todas las empresas vía `tools.migrate_tenants` (tabla
vacía / columna NULL no pesa; el acceso lo gatean las feature flags).

Piezas:
  - ALTER `gastos`: imputación a obra/máquina + metadatos de caja menor y del bot (spec 09). Las columnas
    de dominio (obra_id, maquina_id, categoría, método de pago, comprobante, telegram_*) son NULLABLE. Dos
    NO son nulas por diseño y se rellenan con server_default en las filas ya existentes: `origen_registro`
    (NOT NULL default MANUAL — la spec lo modela no-nulo `@default(MANUAL)`, y así las filas del POS
    quedan MANUAL, que es lo correcto: se capturaron a mano) y `requiere_revision` (NOT NULL default
    false — el POS nunca deja gastos pendientes de revisión; solo el bot con baja confianza los marca).
  - ALTER `compras`: imputación a obra + resbalo del viaje de material (spec 11). `es_viaje_material` NOT
    NULL default false (las compras del POS NO son viajes de material). El dinero nuevo va en MONEY4
    (18,4, spec construcción) aunque `compras.total`/`compras_detalle.costo` sigan en MONEY (12,2, POS):
    divergencia DOCUMENTADA en core/money.py, no se mezclan.
  - CREATE `liquidaciones_obra`: snapshot INMUTABLE del cierre de una obra (spec 04 «immutable snapshot»).
    Congela el gasto real desglosado (los 5 componentes de `services.calculations.obra.DesgloseGasto` +
    total), el presupuesto, la utilidad real y el semáforo del día que se liquidó. `obra_id` UNIQUE = una
    liquidación por obra (la operación de liquidar es idempotente: reintentar choca contra el UNIQUE).
    `snapshot_json` (JSONB) guarda el detalle completo por si el algoritmo de cálculo cambia luego: el
    número liquidado es histórico y no se recalcula.

Enums nuevos de esta migración (literales EXACTOS a la spec 01_MODELO_DATOS, en MAYÚSCULAS salvo el
semáforo): `categoria_gasto` (CategoriaGasto), `metodo_pago_gasto` (MetodoPago), `categoria_compra`
(CategoriaCompra) y `semaforo_obra` (verde/amarillo/rojo — en minúscula a propósito: espeja los `.value`
de `services.calculations.obra.Semaforo`, para que el service guarde `desglose.semaforo.value` directo).

Nota de nombre de TIPO: el enum de método de pago se llama `metodo_pago_gasto` (no `metodo_pago`) porque
YA existe un tipo `metodo_pago` del POS (enum de ventas, dueño 0007: efectivo/transferencia/datafono/…).
La COLUMNA en `gastos` sí se llama `metodo_pago` (la spec la nombra `metodoPago`); solo el tipo lleva el
sufijo para no chocar con el enum de ventas.
`origen_registro` (MANUAL/TELEGRAM_BOT/IMPORTACION) NO se crea aquí: es dueño 0044; se REUSA
(create_type=False) en `gastos` y el downgrade de esta migración NO lo dropea.

Nota de nombre de columna: en `gastos` la columna de categoría del vertical se llama `categoria_gasto`
(no `categoria`) porque la tabla YA tiene una columna `categoria` del POS (enum `gasto_categoria`:
transporte/papelería/…). Las dos taxonomías conviven en la misma tabla; no se puede duplicar el nombre.
En `compras` no hay colisión, así que ahí la columna nueva sí se llama `categoria`.

Revision ID: 0048_gastos_compras_liquidacion
Revises: 0047_nomina_liquidacion
Create Date: 2026-07-06

Nota: el `revision` id (`0048_gastos_compras_liquidacion`, 31 chars) cabe en `alembic_version.version_num`
(VARCHAR(32)); el archivo conserva el mismo nombre.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_gastos_compras_liquidacion"
down_revision: str | None = "0047_nomina_liquidacion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MONEY4 = sa.Numeric(18, 4)   # dinero del vertical construcción (spec @db.Decimal(18,4))

# Enums nuevos (literales EXACTOS a la spec 01_MODELO_DATOS). `origen_registro` es de 0044 (se reusa).
_CATEGORIA_GASTO = (
    "REPUESTOS", "MANTENIMIENTO_MAQUINA", "ALMUERZOS", "TRANSPORTE_PERSONAL", "COMBUSTIBLE",
    "PAPELERIA", "SERVICIOS_PUBLICOS", "ARRIENDO", "IMPUESTOS", "OTRO",
)
_METODO_PAGO = (
    "EFECTIVO", "TRANSFERENCIA_BANCOLOMBIA", "TRANSFERENCIA_OTRO_BANCO", "TARJETA_CREDITO",
    "TARJETA_DEBITO", "CHEQUE",
)
_CATEGORIA_COMPRA = (
    "MEZCLA_ASFALTICA", "EMULSION_ASFALTICA", "ARENA_AGREGADO", "REPUESTO", "COMBUSTIBLE_GENERAL",
    "TRANSPORTE", "SERVICIO_MANTENIMIENTO", "OTRO",
)
# Semáforo de rentabilidad: minúsculas para espejar `services.calculations.obra.Semaforo` (.value).
_SEMAFORO_OBRA = ("verde", "amarillo", "rojo")
_ORIGEN_REGISTRO = ("MANUAL", "TELEGRAM_BOT", "IMPORTACION")   # ya existe (0044): solo se referencia


def upgrade() -> None:
    for nombre, valores in (
        ("categoria_gasto", _CATEGORIA_GASTO),
        ("metodo_pago_gasto", _METODO_PAGO),
        ("categoria_compra", _CATEGORIA_COMPRA),
        ("semaforo_obra", _SEMAFORO_OBRA),
    ):
        etiquetas = ", ".join(f"'{v}'" for v in valores)
        op.execute(f"CREATE TYPE {nombre} AS ENUM ({etiquetas})")

    # --- gastos: imputación a obra/máquina + caja menor (spec 09). Todas NULLABLE salvo las 2 con
    # server_default (que rellenan las filas del POS ya existentes). obra_id/maquina_id con SET NULL:
    # borrar una obra/máquina no debe tumbar ni bloquear un gasto histórico (imputación opcional). --------
    op.add_column(
        "gastos",
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id", ondelete="SET NULL")),
    )
    op.add_column(
        "gastos",
        sa.Column("maquina_id", sa.BigInteger, sa.ForeignKey("maquinas.id", ondelete="SET NULL")),
    )
    # Categoría del vertical: columna `categoria_gasto` (NO `categoria`, que ya existe en el POS).
    op.add_column(
        "gastos",
        sa.Column(
            "categoria_gasto",
            postgresql.ENUM(*_CATEGORIA_GASTO, name="categoria_gasto", create_type=False),
        ),
    )
    op.add_column(
        "gastos",
        sa.Column(
            "metodo_pago",
            postgresql.ENUM(*_METODO_PAGO, name="metodo_pago_gasto", create_type=False),
        ),
    )
    op.add_column("gastos", sa.Column("numero_referencia", sa.Text))   # comprobante Bancolombia
    op.add_column("gastos", sa.Column("comprobante_url", sa.Text))     # captura almacenada (Cloudinary)
    # origen_registro NOT NULL default MANUAL (spec no-nulo @default(MANUAL)); backfill de filas del POS.
    op.add_column(
        "gastos",
        sa.Column(
            "origen_registro",
            postgresql.ENUM(*_ORIGEN_REGISTRO, name="origen_registro", create_type=False),
            nullable=False, server_default="MANUAL",
        ),
    )
    op.add_column("gastos", sa.Column("telegram_user_id", sa.Text))
    op.add_column("gastos", sa.Column("telegram_message_id", sa.Text))
    # requiere_revision NOT NULL default false: el bot marca los de baja confianza; el POS nunca.
    op.add_column(
        "gastos",
        sa.Column(
            "requiere_revision", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
    )
    op.create_index("ix_gastos_obra_id", "gastos", ["obra_id"])

    # --- compras: imputación a obra + resbalo del viaje de material (spec 11). obra_id SET NULL (imputación
    # opcional). Dinero nuevo en MONEY4; `es_viaje_material` NOT NULL default false. -----------------------
    op.add_column(
        "compras",
        sa.Column("obra_id", sa.BigInteger, sa.ForeignKey("obras.id", ondelete="SET NULL")),
    )
    op.add_column(
        "compras",
        sa.Column(
            "es_viaje_material", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
    )
    op.add_column("compras", sa.Column("precio_venta_cliente", _MONEY4))   # lo que se le cobra al cliente
    op.add_column("compras", sa.Column("resbalo", _MONEY4))                # = precio_venta − costo_total
    op.add_column(
        "compras",
        sa.Column(
            "categoria",
            postgresql.ENUM(*_CATEGORIA_COMPRA, name="categoria_compra", create_type=False),
        ),
    )
    op.add_column("compras", sa.Column("factura_url", sa.Text))
    op.create_index("ix_compras_obra_id", "compras", ["obra_id"])

    # --- liquidaciones_obra: snapshot INMUTABLE del cierre de obra (spec 04). obra_id UNIQUE = una por
    # obra (idempotencia del liquidar). Congela DesgloseGasto (5 componentes + total), presupuesto,
    # utilidad real y semáforo; snapshot_json guarda el detalle completo. NOT NULL en el núcleo del
    # snapshot: se crea siempre completo (el service provee todos los valores). ----------------------------
    op.create_table(
        "liquidaciones_obra",
        sa.Column("id", sa.BigInteger, primary_key=True),
        # 1-1 con la obra (UNIQUE). NOT NULL: una liquidación pertenece a una obra. Sin ondelete: no se
        # puede borrar una obra ya liquidada (registro histórico; la obra usa soft delete de todos modos).
        sa.Column(
            "obra_id", sa.BigInteger, sa.ForeignKey("obras.id"), nullable=False, unique=True
        ),
        sa.Column(
            "fecha_liquidacion", sa.TIMESTAMP(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        # Presupuesto (de la cotización GANADA) contra el que se comparó.
        sa.Column("ingreso_presupuestado", _MONEY4, nullable=False),
        sa.Column("utilidad_presupuestada", _MONEY4, nullable=False),
        # Gasto real total (= DesgloseGasto.total) y su desglose congelado por componente.
        sa.Column("gasto_total", _MONEY4, nullable=False),
        sa.Column("total_gastos", _MONEY4, nullable=False),
        sa.Column("total_compras", _MONEY4, nullable=False),
        sa.Column("total_prorrateo_nomina", _MONEY4, nullable=False),
        sa.Column("total_horas_maquina", _MONEY4, nullable=False),
        sa.Column("total_consumos_inventario", _MONEY4, nullable=False),
        # Utilidad real (= ingreso_presupuestado − gasto_total) y semáforo del día que se liquidó.
        sa.Column("utilidad_real", _MONEY4, nullable=False),
        sa.Column(
            "semaforo",
            postgresql.ENUM(*_SEMAFORO_OBRA, name="semaforo_obra", create_type=False),
            nullable=False,
        ),
        # Detalle completo del cálculo (por si el algoritmo cambia luego): el número liquidado es histórico.
        sa.Column("snapshot_json", postgresql.JSONB, nullable=False),
        sa.Column(
            "creado_en", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    # Orden inverso: la tabla nueva; luego columnas de compras y gastos (índices primero); por último los
    # tipos enum PROPIOS de esta migración. `origen_registro` NO se toca (dueño 0044).
    op.drop_table("liquidaciones_obra")

    op.drop_index("ix_compras_obra_id", table_name="compras")
    op.drop_column("compras", "factura_url")
    op.drop_column("compras", "categoria")
    op.drop_column("compras", "resbalo")
    op.drop_column("compras", "precio_venta_cliente")
    op.drop_column("compras", "es_viaje_material")
    op.drop_column("compras", "obra_id")

    op.drop_index("ix_gastos_obra_id", table_name="gastos")
    op.drop_column("gastos", "requiere_revision")
    op.drop_column("gastos", "telegram_message_id")
    op.drop_column("gastos", "telegram_user_id")
    op.drop_column("gastos", "origen_registro")
    op.drop_column("gastos", "comprobante_url")
    op.drop_column("gastos", "numero_referencia")
    op.drop_column("gastos", "metodo_pago")
    op.drop_column("gastos", "categoria_gasto")
    op.drop_column("gastos", "maquina_id")
    op.drop_column("gastos", "obra_id")

    for nombre in ("semaforo_obra", "categoria_compra", "metodo_pago_gasto", "categoria_gasto"):
        op.execute(f"DROP TYPE {nombre}")
