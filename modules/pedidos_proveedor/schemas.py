"""Contratos Pydantic de pedidos a proveedor (api-contract §pedidos-proveedor).

Captura FLEXIBLE (decisión del dueño, ADR de la fase): el pedido se registra rápido (proveedor +
descripción + monto estimado) o detallado (`lineas[]`); lo preciso —productos, cantidades y costos
reales— se fija al RECIBIR la mercancía. El lead time viaja derivado (`horas_transcurridas` /
`lead_time_horas`), nunca almacenado.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CondicionPago = Literal["contado", "credito", "anticipado"]

MAX_MONTO = Decimal("1000000000")


class ProveedorRef(BaseModel):
    """Proveedor por id existente o por nombre/nit (get-or-create, mismo contrato que compras)."""

    id: int | None = Field(default=None, gt=0)
    nombre: str | None = Field(default=None, min_length=1, max_length=200)
    nit: str | None = None

    @model_validator(mode="after")
    def _alguno(self) -> "ProveedorRef":
        if self.id is None and not self.nombre:
            raise ValueError("proveedor requiere id o nombre")
        return self


class LineaPedidoCrear(BaseModel):
    producto_id: int | None = Field(default=None, gt=0)
    descripcion: str | None = Field(default=None, max_length=300)
    cantidad: Decimal = Field(gt=0)
    costo_estimado: Decimal | None = Field(default=None, ge=0, le=MAX_MONTO)

    @model_validator(mode="after")
    def _identificable(self) -> "LineaPedidoCrear":
        if self.producto_id is None and not self.descripcion:
            raise ValueError("la línea requiere producto_id o descripcion")
        return self


class PedidoCrear(BaseModel):
    proveedor: ProveedorRef
    descripcion: str | None = Field(default=None, max_length=500)
    monto_estimado: Decimal | None = Field(default=None, gt=0, le=MAX_MONTO)
    fecha_estimada: date | None = None
    lineas: list[LineaPedidoCrear] = Field(default_factory=list, max_length=200)
    notas: str | None = Field(default=None, max_length=1000)
    # Pago por adelantado: monto entregado al proveedor al hacer el pedido. `anticipo_desde_caja`
    # postea el egreso en la caja abierta (exige caja); False = salió de otra fuente (solo registra).
    anticipo: Decimal | None = Field(default=None, gt=0, le=MAX_MONTO)
    anticipo_desde_caja: bool = False
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def _con_sustancia(self) -> "PedidoCrear":
        if not self.lineas and not self.descripcion:
            raise ValueError("el pedido requiere descripcion o al menos una línea")
        return self


class PedidoEditar(BaseModel):
    """Edición de un pedido EN CAMINO (estado `pedido`): datos de captura, no el reloj ni el estado."""

    descripcion: str | None = Field(default=None, max_length=500)
    monto_estimado: Decimal | None = Field(default=None, gt=0, le=MAX_MONTO)
    fecha_estimada: date | None = None
    lineas: list[LineaPedidoCrear] | None = Field(default=None, max_length=200)
    notas: str | None = Field(default=None, max_length=1000)


class LineaRecibir(BaseModel):
    """Línea REAL de la mercancía que llegó: producto de catálogo + cantidad + costo real.

    `cantidad_fisica` (opcional) es el cuadre de inventario progresivo: cuántas unidades hay
    FÍSICAMENTE tras acomodar la mercancía. Si viene, el service fija el stock a ese absoluto
    (conteo set-to-absolute, sella `inventario.cuadrado_at`) en la misma transacción.
    """

    producto_id: int = Field(gt=0)
    cantidad: Decimal = Field(gt=0)
    costo: Decimal = Field(ge=0, le=MAX_MONTO)
    cantidad_fisica: Decimal | None = Field(default=None, ge=0)


class RecibirPedido(BaseModel):
    lineas: list[LineaRecibir] = Field(min_length=1, max_length=200)
    condicion_pago: CondicionPago
    # Crédito: nº de factura del proveedor (PK de facturas_proveedores) y vencimiento opcionales.
    numero_factura: str | None = Field(default=None, min_length=1, max_length=100)
    fecha_vencimiento: date | None = None
    # Contado: si el pago sale de la caja física ahora (exige caja abierta).
    pago_desde_caja: bool = False
    notas: str | None = Field(default=None, max_length=1000)
    idempotency_key: str | None = None


class LineaPedidoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    producto_id: int | None
    descripcion: str | None
    cantidad: Decimal
    costo_estimado: Decimal | None


class PedidoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proveedor_id: int
    proveedor_nombre: str | None = None
    fecha_pedido: datetime
    fecha_estimada: date | None
    estado: str
    descripcion: str | None
    monto_estimado: Decimal | None
    anticipo: Decimal | None
    fecha_recepcion: datetime | None
    compra_id: int | None
    factura_proveedor_id: str | None
    condicion_pago: str | None
    notas: str | None
    detalles: list[LineaPedidoLeer] = []
    # Derivados del cronómetro (nunca persistidos):
    horas_transcurridas: float | None = None      # solo pedidos en camino
    lead_time_horas: float | None = None          # solo recibidos
    promedio_proveedor_horas: float | None = None  # histórico del proveedor (semáforo)


class CuadreLinea(BaseModel):
    """Resultado del cuadre de inventario de una línea recibida (inventario progresivo)."""

    producto_id: int
    stock_previo: Decimal
    stock_resultante: Decimal
    cuadrado: bool   # True si vino `cantidad_fisica` y el stock quedó fijado al físico


class RecepcionLeer(BaseModel):
    pedido: PedidoLeer
    compra_id: int
    factura_proveedor_id: str | None = None
    lineas: list[CuadreLinea] = []
    replay: bool = False


class MetricasProveedor(BaseModel):
    proveedor_id: int
    proveedor_nombre: str
    pedidos_recibidos: int
    lead_time_promedio_horas: float | None
    ultima_entrega: datetime | None
    pedidos_en_camino: int
    mas_viejo_en_camino_horas: float | None
