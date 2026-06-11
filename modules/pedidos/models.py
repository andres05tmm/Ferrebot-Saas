"""Modelos del pack pedidos (ADR 0016 / tenant 0019).

El menú NO vive aquí: es el catálogo del POS (`productos` + `inventario`), que este pack solo LEE.
Aquí vive el ciclo del pedido (con snapshot de nombre/precio por ítem: el catálogo puede cambiar
después, el pedido no) y la configuración de la cocina/domicilios.
"""
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, Time, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

MONEY = Numeric(12, 2)

pedido_estado = PgEnum(
    "recibido", "confirmado", "en_preparacion", "en_camino", "entregado", "cancelado",
    name="pedido_estado", create_type=False,
)

# Transiciones válidas del ciclo (el dashboard avanza; cancelar solo desde estados no finales).
TRANSICIONES: dict[str, frozenset[str]] = {
    "recibido": frozenset({"confirmado", "cancelado"}),
    "confirmado": frozenset({"en_preparacion", "cancelado"}),
    "en_preparacion": frozenset({"en_camino", "entregado", "cancelado"}),
    "en_camino": frozenset({"entregado", "cancelado"}),
    "entregado": frozenset(),
    "cancelado": frozenset(),
}


class PedidoConfig(TenantBase):
    __tablename__ = "pedido_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    hora_apertura: Mapped[time] = mapped_column(Time, nullable=False, default=time(8, 0))
    hora_cierre: Mapped[time] = mapped_column(Time, nullable=False, default=time(21, 0))
    minimo_pedido: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    tiempo_estimado_min: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    costo_domicilio_default: Mapped[Decimal] = mapped_column(
        MONEY, nullable=False, default=Decimal("0")
    )


class ZonaDomicilio(TenantBase):
    __tablename__ = "zonas_domicilio"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    tarifa: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Pedido(TenantBase):
    __tablename__ = "pedidos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_nombre: Mapped[str | None] = mapped_column(Text)
    cliente_telefono: Mapped[str] = mapped_column(Text, nullable=False)
    direccion: Mapped[str | None] = mapped_column(Text)
    zona_id: Mapped[int | None] = mapped_column(BigInteger)
    costo_domicilio: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    metodo_pago: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(pedido_estado, nullable=False, default="recibido")
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    notas: Mapped[str | None] = mapped_column(Text)
    origen: Mapped[str] = mapped_column(Text, nullable=False, default="whatsapp")
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["PedidoItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class PedidoItem(TenantBase):
    __tablename__ = "pedido_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pedido_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pedidos.id", ondelete="CASCADE"), nullable=False
    )
    producto_id: Mapped[int | None] = mapped_column(BigInteger)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    precio_unitario: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
