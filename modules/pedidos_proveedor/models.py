"""Modelos de pedidos a proveedor (tenant 0052) — la orden ANTES de que llegue la mercancía.

NO confundir con `modules.pedidos` (domicilios de CLIENTE, ADR 0016). Aquí vive el cronómetro de
lead time del negocio: se registra el pedido al proveedor (arranca el reloj), y al llegar la
mercancía se marca recibido (el service crea la compra real → inventario/costo, y la deuda o el
pago). Tablas de negocio sin `empresa_id`: la base ES la frontera del tenant.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

MONEY = Numeric(12, 2)
QTY = Numeric(12, 3)

# Los TIPOS los crea la migración 0052 (create_type=False): aquí solo se mapean.
pedido_prov_estado = PgEnum(
    "pedido", "recibido", "cancelado", name="pedido_prov_estado", create_type=False
)
pedido_prov_condicion = PgEnum(
    "contado", "credito", "anticipado", name="pedido_prov_condicion", create_type=False
)


class PedidoProveedor(TenantBase):
    __tablename__ = "pedidos_proveedor"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proveedor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("proveedores.id"), nullable=False
    )
    fecha_pedido: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_estimada: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(
        pedido_prov_estado, nullable=False, server_default="pedido"
    )
    descripcion: Mapped[str | None] = mapped_column(Text)
    monto_estimado: Mapped[Decimal | None] = mapped_column(MONEY)
    # Pago por adelantado (proveedores que cobran al pedir): monto entregado + ancla del egreso de
    # caja que lo pagó (candado anti-doble-egreso; NULL si el anticipo no salió de la caja).
    anticipo: Mapped[Decimal | None] = mapped_column(MONEY)
    anticipo_movimiento_id: Mapped[int | None] = mapped_column(BigInteger)
    fecha_recepcion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    compra_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("compras.id", ondelete="SET NULL")
    )
    factura_proveedor_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("facturas_proveedores.id", ondelete="SET NULL")
    )
    condicion_pago: Mapped[str | None] = mapped_column(pedido_prov_condicion)
    # FK a usuarios existe en la BD (migración 0052); el ORM no la modela (no hay modelo Usuario),
    # como caja/ventas/facturas_proveedores.
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    notas: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    # Dedup del cron de pedidos demorados (Fase 6, patrón pagar_avisos.ultimo_aviso_en).
    ultimo_aviso_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    detalles: Mapped[list["PedidoProveedorDetalle"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class PedidoProveedorDetalle(TenantBase):
    __tablename__ = "pedidos_proveedor_detalle"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pedido_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pedidos_proveedor.id", ondelete="CASCADE"), nullable=False
    )
    # NULL = línea libre ("lo de siempre", "3 cajas de puntilla"): la captura del pedido es flexible;
    # el detalle preciso (producto de catálogo + costo real) se fija al RECIBIR.
    producto_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("productos.id"))
    descripcion: Mapped[str | None] = mapped_column(Text)
    cantidad: Mapped[Decimal] = mapped_column(QTY, nullable=False)
    costo_estimado: Mapped[Decimal | None] = mapped_column(MONEY)
