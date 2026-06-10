"""Modelo ORM de facturación electrónica (schema.md / tenant 0001).

Tabla de negocio sin `empresa_id`: la base ES la frontera del tenant. El consecutivo sale de la
SEQUENCE `fe_factura_consecutivo_seq` (no `MAX()+1`); `idempotency_key` UNIQUE da la idempotencia
de emisión. Espejo de `modules/ventas/models.py`.
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

fe_tipo_enum = PgEnum(
    "factura", "documento_soporte", "nota_credito", "nota_debito", "pos",
    name="fe_tipo", create_type=False,
)
fe_estado_enum = PgEnum(
    "pendiente", "enviada", "aceptada", "rechazada", "error",
    name="fe_estado", create_type=False,
)


class FacturaElectronica(TenantBase):
    __tablename__ = "facturas_electronicas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    venta_id: Mapped[int | None] = mapped_column(BigInteger)
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
