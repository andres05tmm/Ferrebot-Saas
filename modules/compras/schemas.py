"""Contratos Pydantic de compras (api-contract.md §compras)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Categoría de compra del vertical construcción (spec 11). Literales EXACTOS a la spec 01_MODELO_DATOS
# y al enum `categoria_compra` (tenant 0048).
CategoriaCompra = Literal[
    "MEZCLA_ASFALTICA", "EMULSION_ASFALTICA", "ARENA_AGREGADO", "REPUESTO", "COMBUSTIBLE_GENERAL",
    "TRANSPORTE", "SERVICIO_MANTENIMIENTO", "OTRO",
]


class ProveedorRef(BaseModel):
    """Referencia a un proveedor: por `id` existente, o por `nombre` (+nit) para get-or-create."""

    id: int | None = None
    nombre: str | None = None
    nit: str | None = None

    @model_validator(mode="after")
    def _id_o_nombre(self) -> "ProveedorRef":
        if self.id is None and not (self.nombre and self.nombre.strip()):
            raise ValueError("El proveedor requiere `id` o `nombre`")
        return self


class CompraItemCrear(BaseModel):
    """Una línea de la compra: el producto, la cantidad recibida y su costo unitario.

    `producto_id` es opcional para las compras del vertical construcción imputadas a obra o marcadas
    como viaje de material: esas no llevan producto de catálogo (asfalto/arena no son SKU del POS). En
    una compra de catálogo (la que mueve stock) es OBLIGATORIO — lo valida `CompraCrear`.
    """

    producto_id: int | None = None
    cantidad: Decimal = Field(gt=0)
    costo: Decimal = Field(ge=0)


class CompraCrear(BaseModel):
    """Cuerpo del POST /compras: proveedor + items. El total lo calcula el servidor."""

    proveedor: ProveedorRef
    fecha: date | None = None
    items: list[CompraItemCrear] = Field(min_length=1)
    # Idempotencia (ai-tools.md §4): la fija el cliente/bot. En REST llega por el header
    # `Idempotency-Key` (el router la copia aquí). Misma key + mismo payload → la compra original;
    # misma key + payload distinto → idempotencia_conflicto.
    idempotency_key: str | None = None
    # --- Vertical construcción (spec 11). Todo OPCIONAL: el POS retail no lo usa. ---
    # `obra_id` imputa la compra a una obra (NO mueve stock, solo gasto). `es_viaje_material` marca los
    # viajes de asfalto/arena con resbalo (margen): entonces `precio_venta_cliente` es obligatorio.
    obra_id: int | None = None
    categoria: CategoriaCompra | None = None
    es_viaje_material: bool = False
    precio_venta_cliente: Decimal | None = Field(default=None, ge=0)
    factura_url: str | None = None

    @property
    def imputa_a_obra(self) -> bool:
        """True si la compra se imputa (obra o viaje de material): entonces NO mueve stock (spec 11)."""
        return self.obra_id is not None or self.es_viaje_material

    @model_validator(mode="after")
    def _validar_vertical(self) -> "CompraCrear":
        # Un viaje de material necesita el precio de venta al cliente para computar el resbalo (spec 11).
        if self.es_viaje_material and self.precio_venta_cliente is None:
            raise ValueError("Un viaje de material requiere `precio_venta_cliente` para calcular el resbalo")
        # La compra de catálogo (mueve stock) exige `producto_id` en cada línea; la imputada a obra no.
        if not self.imputa_a_obra:
            for it in self.items:
                if it.producto_id is None:
                    raise ValueError("Una compra de catálogo requiere `producto_id` en cada ítem")
        return self


class CompraLeer(BaseModel):
    """Vista de salida de una compra (cabecera con el nombre del proveedor y el total).

    Los campos del vertical construcción caen a su default para el POS retail (backward-compatible).
    `resbalo_pct`/`resbalo_alerta`/`alerta_precio_proveedor` son DERIVADOS: los llena el servicio (no se
    persisten como tal), por eso viven con default aquí.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    proveedor_id: int | None
    proveedor_nombre: str | None
    fecha: datetime
    total: Decimal
    # --- Vertical construcción (spec 11) ---
    obra_id: int | None = None
    categoria: str | None = None
    es_viaje_material: bool = False
    precio_venta_cliente: Decimal | None = None
    resbalo: Decimal | None = None            # monto = precio_venta_cliente − costo_total (persistido)
    resbalo_pct: Decimal | None = None        # % del margen sobre la venta (derivado)
    resbalo_alerta: bool = False              # margen < 5% o negativo (derivado)
    factura_url: str | None = None
    mueve_stock: bool = True                  # False si se imputó a obra/viaje (no tocó inventario)
    alerta_precio_proveedor: bool = False     # precio > 15% sobre el promedio 6m del proveedor (derivado)


class AnalisisPrecioProveedor(BaseModel):
    """Fila del análisis de precios de proveedor (Fase 8, spec 10): agregado por (proveedor, categoría).

    Vista de solo lectura para vigilar a los proveedores: costo unitario PONDERADO del período, su rango
    (min/max) y la señal de alerta (el peor costo superó en >15% el promedio del proveedor). `variacion_pct`
    = cuánto por encima del promedio quedó el costo máximo (% derivado). Ayuda a detectar sobreprecios sin
    esperar a que muerdan el margen del viaje de material.
    """

    proveedor_id: int | None
    proveedor_nombre: str | None
    categoria: str | None
    n_compras: int
    cantidad_total: Decimal
    costo_unitario_promedio: Decimal
    costo_unitario_min: Decimal
    costo_unitario_max: Decimal
    variacion_pct: Decimal                    # (max − promedio) / promedio × 100 (derivado)
    alerta: bool                              # costo máximo > 15% sobre el promedio del proveedor (derivado)
