"""Cotización AIU, obra y sus registros de campo (vertical construcción, spec cliente 03/04/01 —
tenant 0044/0045).

`CotizacionObra` es una cotización POR AIU (administración/imprevistos/utilidad, IVA solo sobre la
utilidad) — distinta del quote plano de catálogo del POS (`modules.cotizaciones`), por eso vive en su
propia tabla `cotizaciones_obra`. Al GANARse nace una `Obra` (1-1 vía `cotizacion_id` único). La obra
es el corazón del vertical: contra ella se imputan horas de máquina, asistencia, gastos, compras y
consumos para comparar presupuesto vs. real.

`ConsumoInventario` vive en este paquete (dominio "obra") aunque referencie `productos`: es material
imputado a la obra; el MOVIMIENTO de inventario lo emite el service de Fase 3, no el modelo. Siguiendo
el patrón del repo (ver `modules.maquinaria`), las FKs viven en la migración y el ORM mapea los ids
como BigInteger sin `relationship`. Tablas de negocio del tenant (sin `empresa_id`: la base ES la
frontera). Dinero en MONEY4 (18,4); cantidades/porcentajes con la misma precisión que la spec.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase
from core.money import MONEY4

# Cantidad/horas/m²/m³: la spec declara TODO Decimal como 18,4. Porcentaje AIU como fracción 0–1.
CANTIDAD = Numeric(18, 4)
PORCENTAJE = Numeric(6, 4)

# Los tipos los crea la migración 0044 (create_type=False): aquí solo se mapean. Literales EXACTOS.
estado_cotizacion = PgEnum(
    "BORRADOR", "ENVIADA", "GANADA", "PERDIDA", "VENCIDA",
    name="estado_cotizacion", create_type=False,
)
estado_obra = PgEnum(
    "PLANIFICADA", "EN_EJECUCION", "SUSPENDIDA", "FINALIZADA", "LIQUIDADA",
    name="estado_obra", create_type=False,
)
origen_registro = PgEnum(
    "MANUAL", "TELEGRAM_BOT", "IMPORTACION", name="origen_registro", create_type=False
)


class CotizacionObra(TenantBase):
    """Cotización por AIU (spec `Cotizacion`, tabla `cotizaciones_obra`)."""

    __tablename__ = "cotizaciones_obra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    numero: Mapped[str] = mapped_column(Text, nullable=False, unique=True)   # ej. "PIM-001-2026"
    # FK a `clientes.id`: la constraint vive en la migración; el ORM no la modela (patrón del repo).
    cliente_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nombre_obra: Mapped[str] = mapped_column(Text, nullable=False)
    ubicacion: Mapped[str | None] = mapped_column(Text)
    fecha_emision: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    vigencia_dias: Mapped[int] = mapped_column(Integer, nullable=False, server_default="15")

    # Porcentajes AIU (fracción 0–1). El IVA de la cotización recae SOLO sobre la utilidad.
    administracion_pct: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="0"
    )
    imprevistos_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False, server_default="0")
    utilidad_pct: Mapped[Decimal] = mapped_column(PORCENTAJE, nullable=False, server_default="0")
    iva_sobre_utilidad_pct: Mapped[Decimal] = mapped_column(
        PORCENTAJE, nullable=False, server_default="0.19"
    )

    estado: Mapped[str] = mapped_column(
        estado_cotizacion, nullable=False, server_default="BORRADOR"
    )
    condiciones: Mapped[str | None] = mapped_column(Text)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ItemCotizacionObra(TenantBase):
    """Renglón de una cotización AIU (spec `ItemCotizacion`). Se borra en cascada con la cotización."""

    __tablename__ = "items_cotizacion_obra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cotizacion_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cotizaciones_obra.id", ondelete="CASCADE"), nullable=False
    )
    orden: Mapped[int] = mapped_column(Integer, nullable=False)
    descripcion: Mapped[str] = mapped_column(Text, nullable=False)
    unidad: Mapped[str] = mapped_column(Text, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(CANTIDAD, nullable=False)
    valor_unitario: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    # Desglose de costo interno estimado por renglón (alimenta el presupuesto de obra). Nullable.
    costo_material_est: Mapped[Decimal | None] = mapped_column(MONEY4)
    costo_mano_obra_est: Mapped[Decimal | None] = mapped_column(MONEY4)
    costo_equipo_est: Mapped[Decimal | None] = mapped_column(MONEY4)


class Obra(TenantBase):
    """Obra en ejecución (spec `Obra`). Nace de una cotización GANADA (1-1 `cotizacion_id` único)."""

    __tablename__ = "obras"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 1-1 con la cotización que la originó (UNIQUE en la migración); nullable (la FK es opcional).
    cotizacion_id: Mapped[int | None] = mapped_column(BigInteger)
    cliente_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    ubicacion: Mapped[str | None] = mapped_column(Text)
    fecha_inicio: Mapped[date | None] = mapped_column(Date)
    fecha_fin_estimada: Mapped[date | None] = mapped_column(Date)
    fecha_fin_real: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(estado_obra, nullable=False, server_default="PLANIFICADA")
    notas: Mapped[str | None] = mapped_column(Text)

    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    eliminado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # soft delete


class ReporteDiarioObra(TenantBase):
    """Bitácora diaria de avance de una obra (spec `ReporteDiarioObra`), normalmente desde el bot."""

    __tablename__ = "reportes_diarios_obra"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    obra_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    reportado_por: Mapped[str | None] = mapped_column(Text)   # trabajador o supervisor
    telegram_user_id: Mapped[str | None] = mapped_column(Text)
    avance_descripcion: Mapped[str | None] = mapped_column(Text)
    m2_ejecutados: Mapped[Decimal | None] = mapped_column(CANTIDAD)
    m3_ejecutados: Mapped[Decimal | None] = mapped_column(CANTIDAD)
    incidentes: Mapped[str | None] = mapped_column(Text)
    foto_urls: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    origen_registro: Mapped[str] = mapped_column(
        origen_registro, nullable=False, server_default="TELEGRAM_BOT"
    )
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConsumoInventario(TenantBase):
    """Material del catálogo (`productos`) imputado a una obra (spec `ConsumoInventario`).

    La fila NO mueve stock: el movimiento de inventario lo emite el service de Fase 3 (invariante "nada
    mueve stock sin movimiento"). `producto_id` reusa el catálogo POS existente (la spec lo modelaba
    como `ItemInventario`, aquí se mapea a `productos.id`).
    """

    __tablename__ = "consumos_inventario"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(BigInteger, nullable=False)   # FK a productos (migración)
    obra_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(CANTIDAD, nullable=False)
    costo_unitario: Mapped[Decimal] = mapped_column(MONEY4, nullable=False)
    responsable: Mapped[str | None] = mapped_column(Text)
    observaciones: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
