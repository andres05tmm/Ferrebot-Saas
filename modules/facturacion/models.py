"""Modelo ORM de facturación electrónica (schema.md / tenant 0001).

Tabla de negocio sin `empresa_id`: la base ES la frontera del tenant. El consecutivo sale de la
SEQUENCE `fe_factura_consecutivo_seq` (no `MAX()+1`); `idempotency_key` UNIQUE da la idempotencia
de emisión. Espejo de `modules/ventas/models.py`.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Numeric, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

MONEY = Numeric(12, 2)

fe_tipo_enum = PgEnum(
    "factura", "documento_soporte", "nota_credito", "nota_debito", "pos",
    name="fe_tipo", create_type=False,
)
fe_estado_enum = PgEnum(
    # `enviada` queda RESERVADO (la emisión es síncrona: pendiente → aceptada|rechazada|error); previsto
    # para un futuro modelo de aceptación confirmada por webhook (ver docs/facturacion-dian.md).
    "pendiente", "enviada", "aceptada", "rechazada", "error", "anulada",
    name="fe_estado", create_type=False,
)


class FacturaElectronica(TenantBase):
    __tablename__ = "facturas_electronicas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venta_id: Mapped[int | None] = mapped_column(BigInteger)
    # RASTRO obra→documento (Fase 7 DIAN, migración 0050): liga el documento a la obra que lo originó
    # (spec 15 §1 "facturar desde /obras/[id]"). Columna plana BigInteger —la FK a `obras.id` vive en la
    # base, no en el ORM, como `venta_id`. La EMISIÓN se sigue montando sobre `venta_id` (reuso de
    # `FacturacionService`); esto es solo el vínculo para la vista "facturas de esta obra". NULL en toda
    # factura que no nace de una obra (POS/FE de mostrador).
    obra_id: Mapped[int | None] = mapped_column(BigInteger)
    tipo: Mapped[str] = mapped_column(fe_tipo_enum, nullable=False)
    prefijo: Mapped[str | None] = mapped_column(Text)
    consecutivo: Mapped[int | None] = mapped_column(BigInteger)
    cufe: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(fe_estado_enum, nullable=False, default="pendiente")
    xml_url: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)
    # XML técnico archivado (histórico fiscal 5 años, D7.3): se puebla post-aceptada desde MATIAS.
    xml_contenido: Mapped[str | None] = mapped_column(Text)
    dian_respuesta: Mapped[dict | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    intentos: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    emitido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Cross-tabla (factura_id, cuenta_cobro_id): columnas planas BigInteger sin ForeignKey en el ORM —
# la FK vive en la base (tenant 0001). Mismo criterio que `FacturaElectronica.venta_id`: no acoplar el
# grafo de mappers entre módulos. Estos modelos (ADR 0025) mapean tablas que existían sin ORM.


class NotaElectronica(TenantBase):
    """Nota crédito/débito electrónica asociada a una factura y a la venta origen (tenant 0001 + 0031).

    Los campos de vínculo/numeración/idempotencia (ADR 0026) los agrega la migración 0031: `venta_id`
    liga la nota a la venta corregida; `consecutivo`/`prefijo` son su propio número DIAN; `idempotency_key`
    UNIQUE da la idempotencia de emisión; `dian_respuesta` guarda la respuesta MATIAS completa (histórico
    fiscal). `factura_id`/`venta_id` son columnas planas BigInteger (la FK vive en la base, no en el ORM)."""

    __tablename__ = "notas_electronicas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    factura_id: Mapped[int | None] = mapped_column(BigInteger)
    venta_id: Mapped[int | None] = mapped_column(BigInteger)
    tipo: Mapped[str] = mapped_column(fe_tipo_enum, nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text)
    prefijo: Mapped[str | None] = mapped_column(Text)
    consecutivo: Mapped[int | None] = mapped_column(BigInteger)
    cufe: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(fe_estado_enum, nullable=False, default="pendiente")
    dian_respuesta: Mapped[dict | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    intentos: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    emitido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentoSoporte(TenantBase):
    """Documento soporte (compras a no obligados a facturar) — DIAN (tenant 0001).

    `idempotency_key` UNIQUE da la idempotencia de emisión; el consecutivo sale de la SEQUENCE
    `ds_consecutivo_seq`. `cuenta_cobro_id` referencia la cuenta de cobro que lo originó (FK en la base).
    """

    __tablename__ = "documentos_soporte"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consecutivo: Mapped[str | None] = mapped_column(Text)
    fecha: Mapped[date | None] = mapped_column(Date)
    valor: Mapped[Decimal | None] = mapped_column(MONEY)
    cude: Mapped[str | None] = mapped_column(Text)
    estado_dian: Mapped[str | None] = mapped_column(Text)
    cuenta_cobro_id: Mapped[int | None] = mapped_column(BigInteger)
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    intentos: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    emitido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventoDian(TenantBase):
    """Bitácora de eventos DIAN por factura (envío, acuse, aceptación...) — tenant 0001."""

    __tablename__ = "eventos_dian"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    factura_id: Mapped[int | None] = mapped_column(BigInteger)
    evento: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
