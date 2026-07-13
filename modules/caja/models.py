"""Modelos de caja, movimientos de caja y gastos (schema.md / tenant 0001 + 0003)."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)

caja_estado = PgEnum("abierta", "cerrada", name="caja_estado", create_type=False)
caja_mov_tipo = PgEnum("ingreso", "egreso", name="caja_mov_tipo", create_type=False)
gasto_categoria = PgEnum(
    "transporte", "papeleria", "servicios", "nomina", "mantenimiento", "otros",
    name="gasto_categoria", create_type=False,
)

# --- Vertical construcción (spec 09 / tenant 0048). Los TIPOS los crea la migración 0048
# (create_type=False): aquí solo se mapean. `origen_registro` es dueño 0044 (se reusa). Literales
# EXACTOS a la spec 01_MODELO_DATOS. -----------------------------------------------------------------
categoria_gasto = PgEnum(
    "REPUESTOS", "MANTENIMIENTO_MAQUINA", "ALMUERZOS", "TRANSPORTE_PERSONAL", "COMBUSTIBLE",
    "PAPELERIA", "SERVICIOS_PUBLICOS", "ARRIENDO", "IMPUESTOS", "OTRO",
    name="categoria_gasto", create_type=False,
)
# Tipo `metodo_pago_gasto` (NO `metodo_pago`, que ya existe como enum de ventas del POS, dueño 0007).
# La columna en `gastos` sí se llama `metodo_pago`; solo el TIPO lleva el sufijo para no chocar.
metodo_pago = PgEnum(
    "EFECTIVO", "TRANSFERENCIA_BANCOLOMBIA", "TRANSFERENCIA_OTRO_BANCO", "TARJETA_CREDITO",
    "TARJETA_DEBITO", "CHEQUE",
    name="metodo_pago_gasto", create_type=False,
)
origen_registro = PgEnum(
    "MANUAL", "TELEGRAM_BOT", "IMPORTACION", name="origen_registro", create_type=False
)


class Caja(TenantBase):
    __tablename__ = "caja"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    fecha_apertura: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    saldo_inicial: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    fecha_cierre: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saldo_esperado: Mapped[Decimal | None] = mapped_column(MONEY)
    saldo_contado: Mapped[Decimal | None] = mapped_column(MONEY)
    diferencia: Mapped[Decimal | None] = mapped_column(MONEY)
    estado: Mapped[str] = mapped_column(caja_estado, nullable=False)


class CajaMovimiento(TenantBase):
    __tablename__ = "caja_movimientos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    caja_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tipo: Mapped[str] = mapped_column(caja_mov_tipo, nullable=False)
    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    concepto: Mapped[str | None] = mapped_column(Text)
    referencia: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Gasto(TenantBase):
    __tablename__ = "gastos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    categoria: Mapped[str] = mapped_column(gasto_categoria, nullable=False)
    monto: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    concepto: Mapped[str | None] = mapped_column(Text)
    caja_id: Mapped[int | None] = mapped_column(BigInteger)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    # --- gastos ↔ cuentas por pagar (0036, ADR 0028) --------------------------
    # A quién se le pagó (opcional). El gasto que SALDA una factura de proveedor guarda su id y el
    # ÚNICO abono que generó (candado anti-duplicación: un gasto → a lo sumo un abono).
    proveedor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("proveedores.id", ondelete="SET NULL")
    )
    factura_proveedor_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("facturas_proveedores.id", ondelete="SET NULL")
    )
    abono_proveedor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("facturas_abonos.id", ondelete="SET NULL")
    )
    # --- Vertical construcción (spec 09 / tenant 0048). Imputación a obra/máquina + caja menor + bot. ---
    # Todas NULLABLE salvo `origen_registro` (NOT NULL default MANUAL, espeja la spec no-nula) y
    # `requiere_revision` (NOT NULL default false). Nota: la categoría del vertical es `categoria_gasto`
    # (columna aparte), NO la `categoria` del POS de arriba — dos taxonomías conviven en la tabla.
    obra_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("obras.id", ondelete="SET NULL")
    )
    maquina_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("maquinas.id", ondelete="SET NULL")
    )
    categoria_gasto: Mapped[str | None] = mapped_column(categoria_gasto)
    metodo_pago: Mapped[str | None] = mapped_column(metodo_pago)
    numero_referencia: Mapped[str | None] = mapped_column(Text)   # comprobante Bancolombia
    comprobante_url: Mapped[str | None] = mapped_column(Text)     # captura almacenada (Cloudinary)
    origen_registro: Mapped[str] = mapped_column(
        origen_registro, nullable=False, server_default="MANUAL"
    )
    telegram_user_id: Mapped[str | None] = mapped_column(Text)
    telegram_message_id: Mapped[str | None] = mapped_column(Text)
    requiere_revision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Rechazo de la bandeja (0056): NULL = gasto vivo. El rechazo anula con un INGRESO inverso de caja
    # (nunca delete) y es idempotente por esta columna; los lectores filtran `anulado_en IS NULL`.
    anulado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    motivo_rechazo: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
