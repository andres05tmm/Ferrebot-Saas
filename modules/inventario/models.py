"""Modelos de catálogo e inventario usados por la venta (schema.md).

Solo las columnas que toca la Fase 1; el resto del esquema existe en la base vía migración.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import ENUM as PgEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import TenantBase
from modules.compras.models import Proveedor

mov_inventario_tipo = PgEnum(
    "ENTRADA", "SALIDA", "AJUSTE", "DEVOLUCION",
    name="mov_inventario_tipo", create_type=False,
)


class Producto(TenantBase):
    __tablename__ = "productos"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    codigo: Mapped[str | None] = mapped_column(Text)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    categoria: Mapped[str | None] = mapped_column(Text)
    # Proveedor de la lista registrada (tenant 0006, FK ON DELETE SET NULL); reemplaza la vieja `marca`.
    proveedor_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("proveedores.id", ondelete="SET NULL")
    )
    unidad_medida: Mapped[str] = mapped_column(Text, nullable=False)
    precio_venta: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_compra: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Costo unitario promedio ponderado móvil (ADR 0025): lo recalcula cada COMPRA bajo FOR UPDATE y
    # lo snapshotean las SALIDA en su `costo_unitario`. NULL hasta la primera compra (cae a precio_compra).
    costo_promedio: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    precio_especial: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Precio escalonado por cantidad (modelo FerreBot): NULL si no aplica.
    precio_umbral: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    precio_bajo_umbral: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    precio_sobre_umbral: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    iva: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    permite_fraccion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Zona de comandas KDS (0062, ADR 0032 D5): rutea el producto a parrilla/bar/…; NULL = cocina.
    zona_comanda_id: Mapped[int | None] = mapped_column(BigInteger)
    # Tipo del impuesto de la tarifa en `iva` (0063, ADR 0032 D2): 'iva' (0/5/19) o 'inc'
    # (impoconsumo 8%, restaurantes). El precio sigue siendo FINAL al público en ambos casos.
    tipo_impuesto: Mapped[str] = mapped_column(Text, nullable=False, default="iva")

    fracciones: Mapped[list["ProductoFraccion"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    # Many-to-one al proveedor (selectin: sin N+1 al listar; el FK NULL no dispara consulta).
    proveedor: Mapped["Proveedor | None"] = relationship("Proveedor", lazy="selectin")

    @property
    def proveedor_nombre(self) -> str | None:
        """Nombre del proveedor para mostrar (None si el producto no tiene proveedor asignado)."""
        return self.proveedor.nombre if self.proveedor is not None else None


class ProductoFraccion(TenantBase):
    __tablename__ = "productos_fracciones"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("productos.id", ondelete="CASCADE"), nullable=False
    )
    fraccion: Mapped[str] = mapped_column(Text, nullable=False)
    decimal: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))
    precio_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    precio_unitario: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))


class Alias(TenantBase):
    """Variante/typo de búsqueda → forma canónica; opcionalmente ligada a un producto.

    Alimenta la búsqueda fuzzy y el bypass del bot. `producto_id` NULL = alias global (corrección de
    término que no apunta a un producto concreto). UNIQUE(termino). La tabla la crea la migración
    tenant 0001; aquí solo el mapeo ORM. Sin empresa_id: la base es la frontera del tenant.
    """

    __tablename__ = "aliases"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    termino: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    reemplazo: Mapped[str] = mapped_column(Text, nullable=False)
    producto_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("productos.id"))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actualizado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Inventario(TenantBase):
    __tablename__ = "inventario"

    producto_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stock_actual: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    stock_minimo: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    # Inventario progresivo (0052): sello del último CONTEO físico. NULL = stock aún no confiable
    # (el negocio arranca sin inventario y vende en negativo); lo estampa InventarioService.contar.
    cuadrado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MovimientoInventario(TenantBase):
    __tablename__ = "movimientos_inventario"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    producto_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tipo: Mapped[str] = mapped_column(mov_inventario_tipo, nullable=False)
    cantidad: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    costo_unitario: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    referencia: Mapped[str | None] = mapped_column(Text)
    usuario_id: Mapped[int | None] = mapped_column(BigInteger)
    # Idempotencia estructural (migración 0002): UNIQUE parcial donde no es NULL.
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    # Fecha de la OPERACIÓN de negocio origen (ADR 0025): la fecha de la venta para SALIDA y de la
    # compra para ENTRADA; NULL para ajustes (el P&L cae a `creado_en`). Ancla el COGS a la fecha de
    # la venta, no al instante de inserción del movimiento (que difería al editar una venta).
    fecha_operacion: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
