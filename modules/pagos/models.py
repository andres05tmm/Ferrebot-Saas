"""Modelo de cobros (ADR 0013 / tenant 0021). Infraestructura transversal: los packs la consumen."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase

cobro_estado = PgEnum(
    "pendiente", "pagado", "vencido", "cancelado", name="cobro_estado", create_type=False
)


class Cobro(TenantBase):
    __tablename__ = "cobros"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    referencia: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    origen: Mapped[str] = mapped_column(Text, nullable=False)        # pedido | cita | cobranza | manual
    origen_id: Mapped[int | None] = mapped_column(BigInteger)
    cliente_telefono: Mapped[str | None] = mapped_column(Text)
    monto: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(cobro_estado, nullable=False, default="pendiente")
    proveedor: Mapped[str] = mapped_column(Text, nullable=False, default="manual")  # bold | manual
    proveedor_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Comprobante(TenantBase):
    """Foto del comprobante que manda el cliente (tenant 0057). Auditoría + desempate.

    Cada foto leída por Visión se guarda SIEMPRE (aunque no case). `cobro_id` es una FK lógica a
    `cobros` (NULL si no se pudo asociar). NUNCA marca un cobro pagado: solo lo asocia; el pago lo
    pone el conciliador cuando llega la transferencia real.
    """

    __tablename__ = "comprobantes_pago"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_telefono: Mapped[str] = mapped_column(Text, nullable=False)
    cobro_id: Mapped[int | None] = mapped_column(BigInteger)
    monto: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fecha: Mapped[date | None] = mapped_column(Date)
    referencia: Mapped[str | None] = mapped_column(Text)
    origen: Mapped[str | None] = mapped_column(Text)          # entidad_o_producto_origen
    destino: Mapped[str | None] = mapped_column(Text)
    banco_tipo: Mapped[str | None] = mapped_column(Text)      # tipo_transaccion
    confianza: Mapped[Decimal | None] = mapped_column(Numeric)
    imagen_ref: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
