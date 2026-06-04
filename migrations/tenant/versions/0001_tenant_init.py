"""tenant init — esquema de negocio por empresa (schema.md). Sin empresa_id.

Revision ID: 0001_tenant
Revises:
Create Date: 2026-06-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_tenant"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS = {
    "mov_inventario_tipo": ("ENTRADA", "SALIDA", "AJUSTE", "DEVOLUCION"),
    "venta_estado": ("completada", "anulada"),
    "venta_origen": ("web", "bot", "voz", "offline"),
    "metodo_pago": ("efectivo", "transferencia", "tarjeta", "nequi", "daviplata", "fiado"),
    "caja_estado": ("abierta", "cerrada"),
    "caja_mov_tipo": ("ingreso", "egreso"),
    "gasto_categoria": ("transporte", "papeleria", "servicios", "nomina", "mantenimiento", "otros"),
    "fiado_mov_tipo": ("cargo", "abono"),
    "fe_tipo": ("factura", "documento_soporte", "nota_credito", "nota_debito"),
    "fe_estado": ("pendiente", "enviada", "aceptada", "rechazada", "error"),
    "usuario_rol": ("admin", "vendedor"),
}

# Secuencias de consecutivos (no MAX()+1): ventas, factura electrónica, documento soporte.
_SEQUENCES = ("ventas_consecutivo_seq", "fe_factura_consecutivo_seq", "ds_consecutivo_seq")

MONEY = sa.Numeric(12, 2)
QTY = sa.Numeric(12, 3)


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def _ts(col: str = "creado_en", nullable: bool = False) -> sa.Column:
    return sa.Column(col, sa.TIMESTAMP(timezone=True), nullable=nullable, server_default=sa.text("now()"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, values in _ENUMS.items():
        valores = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({valores})")
    for seq in _SEQUENCES:
        op.execute(f"CREATE SEQUENCE {seq} START 1")

    _usuarios_config_ia()   # usuarios primero: varias FKs apuntan a él
    _catalogo_e_inventario()
    _personas()
    _ventas()
    _compras_y_cxp()
    _caja_y_gastos()
    _fiados_y_honorarios()
    _facturacion_dian()


def _catalogo_e_inventario() -> None:
    op.create_table(
        "productos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("codigo", sa.Text, unique=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("categoria", sa.Text),
        sa.Column("marca", sa.Text),
        sa.Column("unidad_medida", sa.Text, nullable=False),
        sa.Column("precio_venta", MONEY, nullable=False),
        sa.Column("precio_compra", MONEY),
        sa.Column("precio_mayorista", MONEY),
        sa.Column("precio_umbral", QTY),
        sa.Column("precio_bajo_umbral", MONEY),
        sa.Column("precio_sobre_umbral", MONEY),
        sa.Column("iva", sa.SmallInteger, nullable=False, server_default="19"),
        sa.Column("permite_fraccion", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts(),
        _ts("actualizado_en", nullable=True),
    )
    # Búsqueda fuzzy/FTS sobre nombre (ferrebot-logica-portar.md §4).
    op.execute("CREATE INDEX ix_productos_nombre_trgm ON productos USING gin (nombre gin_trgm_ops)")

    op.create_table(
        "productos_fracciones",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("producto_id", sa.BigInteger, sa.ForeignKey("productos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fraccion", sa.Text, nullable=False),
        sa.Column("decimal", QTY),
        sa.Column("precio_total", MONEY, nullable=False),
        sa.Column("precio_unitario", MONEY),
        sa.UniqueConstraint("producto_id", "fraccion", name="uq_producto_fraccion"),
    )

    op.create_table(
        "aliases",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("termino", sa.Text, nullable=False, unique=True),
        sa.Column("reemplazo", sa.Text, nullable=False),
        sa.Column("producto_id", sa.BigInteger, sa.ForeignKey("productos.id")),
        _ts(),
        _ts("actualizado_en", nullable=True),
    )

    op.create_table(
        "inventario",
        sa.Column("producto_id", sa.BigInteger, sa.ForeignKey("productos.id"), primary_key=True),
        sa.Column("stock_actual", QTY, nullable=False, server_default="0"),
        sa.Column("stock_minimo", QTY, nullable=False, server_default="0"),
        _ts("actualizado_en", nullable=True),
    )

    op.create_table(
        "movimientos_inventario",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("producto_id", sa.BigInteger, sa.ForeignKey("productos.id"), nullable=False),
        sa.Column("tipo", _enum("mov_inventario_tipo"), nullable=False),
        sa.Column("cantidad", QTY, nullable=False),
        sa.Column("costo_unitario", MONEY),
        sa.Column("referencia", sa.Text),
        sa.Column("usuario_id", sa.BigInteger, sa.ForeignKey("usuarios.id")),
        _ts(),
    )
    op.create_index("ix_mov_inv_producto_fecha", "movimientos_inventario", ["producto_id", "creado_en"])


def _personas() -> None:
    op.create_table(
        "clientes",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("tipo_documento", sa.Text),
        sa.Column("documento", sa.Text),
        sa.Column("telefono", sa.Text),
        sa.Column("correo", sa.Text),
        sa.Column("direccion", sa.Text),
        sa.Column("ciudad_dane", sa.Text),
        sa.Column("regimen", sa.Text),
        sa.Column("saldo_fiado", MONEY, nullable=False, server_default="0"),
        _ts(),
    )
    op.create_index("ix_clientes_documento", "clientes", ["documento"])

    op.create_table(
        "proveedores",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("nit", sa.Text),
        sa.Column("telefono", sa.Text),
        sa.Column("correo", sa.Text),
        _ts(),
    )


def _ventas() -> None:
    op.create_table(
        "ventas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("consecutivo", sa.BigInteger, nullable=False, unique=True),
        sa.Column("cliente_id", sa.BigInteger, sa.ForeignKey("clientes.id")),
        sa.Column("vendedor_id", sa.BigInteger, sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("fecha", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("subtotal", MONEY, nullable=False),
        sa.Column("impuestos", MONEY, nullable=False),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("metodo_pago", _enum("metodo_pago"), nullable=False),
        sa.Column("estado", _enum("venta_estado"), nullable=False, server_default="completada"),
        sa.Column("origen", _enum("venta_origen"), nullable=False, server_default="web"),
        sa.Column("idempotency_key", sa.Text, unique=True),
    )
    op.create_index("ix_ventas_fecha", "ventas", ["fecha"])
    op.create_index("ix_ventas_vendedor_fecha", "ventas", ["vendedor_id", "fecha"])

    op.create_table(
        "ventas_detalle",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("venta_id", sa.BigInteger, sa.ForeignKey("ventas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("producto_id", sa.BigInteger, sa.ForeignKey("productos.id")),
        sa.Column("descripcion", sa.Text),
        sa.Column("cantidad", QTY, nullable=False),
        sa.Column("precio_unitario", MONEY, nullable=False),
        sa.Column("iva", sa.SmallInteger, nullable=False),
    )

    op.create_table(
        "historico_ventas",
        sa.Column("fecha", sa.Date, primary_key=True),
        sa.Column("ventas", MONEY, nullable=False, server_default="0"),
        sa.Column("efectivo", MONEY, nullable=False, server_default="0"),
        sa.Column("transferencia", MONEY, nullable=False, server_default="0"),
        sa.Column("datafono", MONEY, nullable=False, server_default="0"),
        sa.Column("n_transacciones", sa.Integer, nullable=False, server_default="0"),
        sa.Column("gastos", MONEY, nullable=False, server_default="0"),
        sa.Column("abonos_proveedores", MONEY, nullable=False, server_default="0"),
        sa.Column("origen", sa.Text, nullable=False, server_default="calculado"),
        sa.Column("incluir_en_balances", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("notas", sa.Text),
        _ts("actualizado_en", nullable=True),
    )


def _compras_y_cxp() -> None:
    op.create_table(
        "compras",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("proveedor_id", sa.BigInteger, sa.ForeignKey("proveedores.id")),
        sa.Column("fecha", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("total", MONEY),
        _ts(),
    )
    op.create_table(
        "compras_detalle",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("compra_id", sa.BigInteger, sa.ForeignKey("compras.id", ondelete="CASCADE"), nullable=False),
        sa.Column("producto_id", sa.BigInteger, sa.ForeignKey("productos.id")),
        sa.Column("cantidad", QTY),
        sa.Column("costo", MONEY),
    )
    op.create_table(
        "compras_fiscal",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("compra_id", sa.BigInteger, sa.ForeignKey("compras.id")),
        sa.Column("proveedor_nit", sa.Text),
        sa.Column("base", MONEY),
        sa.Column("iva", MONEY),
        sa.Column("total", MONEY),
        sa.Column("soporte_url", sa.Text),
        sa.Column("cufe_proveedor", sa.Text),
        sa.Column("evento_030_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("evento_031_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("evento_032_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("evento_033_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("evento_estado", sa.Text),
        sa.Column("evento_error", sa.Text),
        _ts(),
    )
    op.create_table(
        "facturas_proveedores",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("proveedor", sa.Text, nullable=False),
        sa.Column("descripcion", sa.Text),
        sa.Column("total", MONEY, nullable=False),
        sa.Column("pagado", MONEY, nullable=False, server_default="0"),
        sa.Column("pendiente", MONEY, nullable=False),
        sa.Column("estado", sa.Text, nullable=False, server_default="pendiente"),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("foto_url", sa.Text),
        sa.Column("foto_nombre", sa.Text),
        sa.Column("usuario_id", sa.BigInteger, sa.ForeignKey("usuarios.id")),
        _ts(),
    )
    op.create_table(
        "facturas_abonos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("factura_id", sa.Text, sa.ForeignKey("facturas_proveedores.id", ondelete="CASCADE")),
        sa.Column("monto", MONEY, nullable=False),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("foto_url", sa.Text),
        sa.Column("foto_nombre", sa.Text),
        _ts(),
    )
    op.create_table(
        "bancolombia_transferencias",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("gmail_message_id", sa.Text, nullable=False, unique=True),
        sa.Column("fecha", sa.Date, nullable=False),
        sa.Column("hora", sa.Text),
        sa.Column("monto", MONEY, nullable=False),
        sa.Column("remitente", sa.Text),
        sa.Column("descripcion", sa.Text),
        sa.Column("tipo_transaccion", sa.Text),
        sa.Column("referencia", sa.Text),
        sa.Column("notificado", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts(),
    )


def _caja_y_gastos() -> None:
    op.create_table(
        "caja",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("usuario_id", sa.BigInteger, sa.ForeignKey("usuarios.id")),
        sa.Column("fecha_apertura", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("saldo_inicial", MONEY, nullable=False),
        sa.Column("fecha_cierre", sa.TIMESTAMP(timezone=True)),
        sa.Column("saldo_esperado", MONEY),
        sa.Column("saldo_contado", MONEY),
        sa.Column("diferencia", MONEY),
        sa.Column("estado", _enum("caja_estado"), nullable=False, server_default="abierta"),
    )
    # Una sola caja abierta por vendedor.
    op.execute("CREATE UNIQUE INDEX uq_caja_abierta_por_usuario ON caja (usuario_id) WHERE estado = 'abierta'")

    op.create_table(
        "caja_movimientos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("caja_id", sa.BigInteger, sa.ForeignKey("caja.id"), nullable=False),
        sa.Column("tipo", _enum("caja_mov_tipo"), nullable=False),
        sa.Column("monto", MONEY, nullable=False),
        sa.Column("concepto", sa.Text),
        sa.Column("referencia", sa.Text),
        _ts(),
    )
    op.create_table(
        "gastos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("categoria", _enum("gasto_categoria"), nullable=False),
        sa.Column("monto", MONEY, nullable=False),
        sa.Column("concepto", sa.Text),
        sa.Column("caja_id", sa.BigInteger, sa.ForeignKey("caja.id")),
        sa.Column("usuario_id", sa.BigInteger, sa.ForeignKey("usuarios.id")),
        _ts(),
    )


def _fiados_y_honorarios() -> None:
    op.create_table(
        "fiados",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("cliente_id", sa.BigInteger, sa.ForeignKey("clientes.id"), nullable=False),
        sa.Column("venta_id", sa.BigInteger, sa.ForeignKey("ventas.id")),
        sa.Column("monto", MONEY),
        sa.Column("saldo", MONEY),
        _ts(),
    )
    op.create_table(
        "fiados_movimientos",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("fiado_id", sa.BigInteger, sa.ForeignKey("fiados.id")),
        sa.Column("tipo", _enum("fiado_mov_tipo"), nullable=False),
        sa.Column("monto", MONEY, nullable=False),
        _ts(),
    )
    op.create_table(
        "cuentas_cobro",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("consecutivo", sa.BigInteger),
        sa.Column("numero_display", sa.Text),
        sa.Column("periodo", sa.Text),
        sa.Column("concepto", sa.Text),
        sa.Column("valor", MONEY),
        sa.Column("cliente_id", sa.BigInteger, sa.ForeignKey("clientes.id")),
        sa.Column("enviado_telegram", sa.Boolean, server_default=sa.false()),
        _ts(),
    )


def _facturacion_dian() -> None:
    op.create_table(
        "facturas_electronicas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("venta_id", sa.BigInteger, sa.ForeignKey("ventas.id")),
        sa.Column("tipo", _enum("fe_tipo"), nullable=False),
        sa.Column("prefijo", sa.Text),
        sa.Column("consecutivo", sa.BigInteger),
        sa.Column("cufe", sa.Text),
        sa.Column("estado", _enum("fe_estado"), nullable=False, server_default="pendiente"),
        sa.Column("xml_url", sa.Text),
        sa.Column("pdf_url", sa.Text),
        sa.Column("dian_respuesta", postgresql.JSONB),
        sa.Column("idempotency_key", sa.Text, unique=True),
        sa.Column("intentos", sa.SmallInteger, nullable=False, server_default="0"),
        _ts(),
        sa.Column("emitido_en", sa.TIMESTAMP(timezone=True)),
    )
    op.create_table(
        "notas_electronicas",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("factura_id", sa.BigInteger, sa.ForeignKey("facturas_electronicas.id")),
        sa.Column("tipo", _enum("fe_tipo"), nullable=False),
        sa.Column("motivo", sa.Text),
        sa.Column("cufe", sa.Text),
        sa.Column("estado", _enum("fe_estado"), nullable=False, server_default="pendiente"),
        _ts(),
    )
    op.create_table(
        "documentos_soporte",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("consecutivo", sa.Text),
        sa.Column("fecha", sa.Date),
        sa.Column("valor", MONEY),
        sa.Column("cude", sa.Text),
        sa.Column("estado_dian", sa.Text),
        sa.Column("cuenta_cobro_id", sa.BigInteger, sa.ForeignKey("cuentas_cobro.id")),
        sa.Column("idempotency_key", sa.Text, unique=True),
        sa.Column("intentos", sa.SmallInteger, nullable=False, server_default="0"),
        _ts(),
        sa.Column("emitido_en", sa.TIMESTAMP(timezone=True)),
    )
    op.create_table(
        "eventos_dian",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("factura_id", sa.BigInteger, sa.ForeignKey("facturas_electronicas.id")),
        sa.Column("evento", sa.Text),
        sa.Column("estado", sa.Text),
        sa.Column("payload", postgresql.JSONB),
        _ts(),
    )
    op.create_table(
        "iva_saldos_bimestrales",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("anio", sa.Integer),
        sa.Column("bimestre", sa.SmallInteger),
        sa.Column("iva_generado", MONEY),
        sa.Column("iva_descontable", MONEY),
        sa.Column("saldo", MONEY),
        sa.UniqueConstraint("anio", "bimestre", name="uq_iva_saldos_periodo"),
    )
    op.create_table(
        "libro_iva",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("fecha", sa.Date),
        sa.Column("tipo", sa.Text),
        sa.Column("base", MONEY),
        sa.Column("iva", MONEY),
        sa.Column("referencia", sa.Text),
        _ts(),
    )


def _usuarios_config_ia() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger, unique=True),
        sa.Column("nombre", sa.Text, nullable=False),
        sa.Column("rol", _enum("usuario_rol"), nullable=False, server_default="vendedor"),
        sa.Column("activo", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts(),
    )
    op.create_table(
        "config_empresa",
        sa.Column("clave", sa.Text, primary_key=True),
        sa.Column("valor", postgresql.JSONB),
    )
    op.create_table(
        "conversaciones_bot",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("chat_id", sa.BigInteger),
        sa.Column("rol", sa.Text),
        sa.Column("contenido", sa.Text),
        _ts(),
    )
    op.create_index("ix_conversaciones_chat_fecha", "conversaciones_bot", ["chat_id", "creado_en"])
    op.create_table(
        "memoria_entidades",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("tipo", sa.Text),
        sa.Column("clave", sa.Text),
        sa.Column("valor", postgresql.JSONB),
        _ts("actualizado_en", nullable=True),
    )
    op.create_table(
        "ventas_pendientes_voz",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("chat_id", sa.BigInteger),
        sa.Column("payload", postgresql.JSONB),
        sa.Column("estado", sa.Text),
        _ts(),
    )
    op.create_table(
        "audio_logs",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("chat_id", sa.BigInteger),
        sa.Column("transcripcion", sa.Text),
        sa.Column("duracion", sa.Integer),
        _ts(),
    )
    op.create_table(
        "api_costo_diario",
        sa.Column("fecha", sa.Date, primary_key=True),
        sa.Column("modelo", sa.Text),
        sa.Column("tokens_in", sa.BigInteger),
        sa.Column("tokens_out", sa.BigInteger),
        sa.Column("costo", sa.Numeric(12, 4)),
    )


def downgrade() -> None:
    for table in (
        "api_costo_diario", "audio_logs", "ventas_pendientes_voz", "memoria_entidades",
        "conversaciones_bot", "config_empresa",
        "libro_iva", "iva_saldos_bimestrales", "eventos_dian", "documentos_soporte",
        "notas_electronicas", "facturas_electronicas",
        "cuentas_cobro", "fiados_movimientos", "fiados",
        "gastos", "caja_movimientos", "caja",
        "bancolombia_transferencias", "facturas_abonos", "facturas_proveedores",
        "compras_fiscal", "compras_detalle", "compras",
        "historico_ventas", "ventas_detalle", "ventas",
        "movimientos_inventario", "inventario", "aliases", "productos_fracciones", "productos",
        "proveedores", "clientes", "usuarios",
    ):
        op.drop_table(table)
    for seq in _SEQUENCES:
        op.execute(f"DROP SEQUENCE IF EXISTS {seq}")
    for name in _ENUMS:
        op.execute(f"DROP TYPE {name}")
