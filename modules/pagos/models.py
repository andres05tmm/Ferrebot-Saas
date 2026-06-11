"""Modelo de cobros (ADR 0013 / tenant 0021). Infraestructura transversal: los packs la consumen."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, Text, func
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
