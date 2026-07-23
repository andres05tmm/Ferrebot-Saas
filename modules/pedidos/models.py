"""Modelos del pack pedidos (ADR 0016 / tenant 0019).

El menú NO vive aquí: es el catálogo del POS (`productos` + `inventario`), que este pack solo LEE.
Aquí vive el ciclo del pedido (con snapshot de nombre/precio por ítem: el catálogo puede cambiar
después, el pedido no) y la configuración de la cocina/domicilios.
"""
from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, Time, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase

MONEY = Numeric(12, 2)

pedido_estado = PgEnum(
    "recibido", "confirmado", "en_preparacion", "en_camino", "entregado", "cancelado", "abierto",
    name="pedido_estado", create_type=False,
)

# Transiciones válidas del ciclo (el dashboard avanza; cancelar solo desde estados no finales).
# `abierto` es la orden de MESA (F3 / ADR 0032 D4): sale por el cobro (puente F1, no por el kanban)
# o por cancelación; los flujos de domicilio jamás entran a él.
TRANSICIONES: dict[str, frozenset[str]] = {
    "recibido": frozenset({"confirmado", "cancelado"}),
    "confirmado": frozenset({"en_preparacion", "cancelado"}),
    "en_preparacion": frozenset({"en_camino", "entregado", "cancelado"}),
    "en_camino": frozenset({"entregado", "cancelado"}),
    "entregado": frozenset(),
    "cancelado": frozenset(),
    "abierto": frozenset({"cancelado"}),
}


class Mesa(TenantBase):
    """Una mesa del salón (F3 / ADR 0032 D4). La orden abierta vive en `pedidos` (origen='mesa')."""

    __tablename__ = "mesas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    zona: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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
    # Recargo POR PLATO de la zona (F6 / ADR 0032 D8, caso Bocagrande +$1.000/plato):
    # costo_domicilio = tarifa + recargo_por_item × Σ cantidades. Default 0 = tarifa plana de siempre.
    recargo_por_item: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Receta(TenantBase):
    """Receta/BOM de un plato (F6 / ADR 0032 D9): vender el plato descuenta sus INSUMOS.

    El plato mismo no lleva stock; el insumo es otro producto del catálogo CON inventario.
    `cantidad` en la unidad del insumo (compatible con fracciones: Numeric 12,3).
    """

    __tablename__ = "recetas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False
    )
    insumo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False
    )
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)


class Pedido(TenantBase):
    __tablename__ = "pedidos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cliente_nombre: Mapped[str | None] = mapped_column(Text)
    cliente_telefono: Mapped[str] = mapped_column(Text, nullable=False)
    # Teléfono REAL de contacto para el domicilio (lo da el cliente al confirmar). Distinto de
    # `cliente_telefono`: en Telegram la identidad es "tg:{chat_id}" y no sirve para llamar.
    telefono_contacto: Mapped[str | None] = mapped_column(Text)
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
    # Puente pedido → venta (F1 / ADR 0032, patrón ADR 0022 D3): UNIQUE, se escribe en la MISMA
    # transacción que la venta con el pedido bajo FOR UPDATE. NULL = aún no convertido.
    venta_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ventas.id", ondelete="SET NULL"), unique=True
    )
    convertido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Mesa de la orden (F3): solo pedidos con origen='mesa'. Índice parcial UNIQUE en la migración
    # 0061 garantiza UNA orden `abierta` por mesa.
    mesa_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("mesas.id", ondelete="SET NULL")
    )
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
    # `precio_unitario` ya incluye los deltas de modificadores (snapshot del total por unidad).
    precio_unitario: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    # Snapshot de modificadores (F2 / ADR 0032 D3): [{grupo, opcion, delta_precio}] al momento del
    # pedido. NULL/[] = sin modificadores. El catálogo puede cambiar después; el pedido no.
    modificadores: Mapped[list | None] = mapped_column(JSONB)


class ModificadorGrupo(TenantBase):
    """Grupo de modificadores de un producto del menú ("Proteína", min=1 max=1; ADR 0032 D3)."""

    __tablename__ = "modificador_grupos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    min_sel: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_sel: Mapped[int | None] = mapped_column(Integer)   # NULL = sin tope
    obligatorio: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    orden: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    opciones: Mapped[list["ModificadorOpcion"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class ComandaZona(TenantBase):
    """Zona de comandas de la cocina (parrilla, bar, …; ADR 0032 D5). NULL en el producto = cocina."""

    __tablename__ = "comanda_zonas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# Transiciones válidas de una comanda (auditadas con timestamps). `pendiente → listo` directo vale
# (plancha rápida); `listo` es terminal.
TRANSICIONES_COMANDA: dict[str, frozenset[str]] = {
    "pendiente": frozenset({"en_preparacion", "listo"}),
    "en_preparacion": frozenset({"listo"}),
    "listo": frozenset(),
}


class Comanda(TenantBase):
    """Una comanda: los ítems de UN pedido que caen en UNA zona (vista de cocina, no copia precios)."""

    __tablename__ = "comandas"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    pedido_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pedidos.id", ondelete="CASCADE"), nullable=False
    )
    zona_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("comanda_zonas.id", ondelete="SET NULL")
    )
    estado: Mapped[str] = mapped_column(Text, nullable=False, default="pendiente")
    creada_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    iniciada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lista_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["ComandaItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class ComandaItem(TenantBase):
    __tablename__ = "comanda_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    comanda_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("comandas.id", ondelete="CASCADE"), nullable=False
    )
    pedido_item_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pedido_items.id", ondelete="CASCADE"), nullable=False
    )
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)


class ModificadorOpcion(TenantBase):
    __tablename__ = "modificador_opciones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    grupo_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("modificador_grupos.id", ondelete="CASCADE"), nullable=False
    )
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    delta_precio: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
