"""Cola de impresión térmica (ADR 0033 D2): un trabajo por ticket, payload determinista."""
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import TenantBase


class TrabajoImpresion(TenantBase):
    __tablename__ = "trabajos_impresion"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tipo: Mapped[str] = mapped_column(Text)   # comanda | precuenta | comprobante
    payload: Mapped[dict] = mapped_column(JSONB)
    zona_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("comanda_zonas.id", ondelete="SET NULL")
    )
    ancho: Mapped[int | None] = mapped_column(SmallInteger)
    estado: Mapped[str] = mapped_column(Text, server_default="pendiente")
    intentos: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    error_detalle: Mapped[str | None] = mapped_column(Text)
    pedido_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("pedidos.id", ondelete="SET NULL")
    )
    comanda_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("comandas.id", ondelete="SET NULL")
    )
    venta_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ventas.id", ondelete="SET NULL")
    )
    reimpresion_de: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("trabajos_impresion.id", ondelete="SET NULL")
    )
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    creado_en: Mapped[datetime] = mapped_column(server_default=func.now())
    entregado_en: Mapped[datetime | None]
    impreso_en: Mapped[datetime | None]
