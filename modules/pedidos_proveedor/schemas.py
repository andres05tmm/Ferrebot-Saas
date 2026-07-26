"""Contratos Pydantic de pedidos a proveedor (api-contract §pedidos-proveedor).

Captura COMPLETA (decisión del dueño, 2026-07-25 — revoca la "captura flexible" del ADR 0031 §2):
el pedido nace con sus productos, cantidades y COSTO UNITARIO, y con la forma de pago declarada.
Al recibir se confirman los números reales (pueden diferir) y después se pueden corregir. El lead
time viaja derivado (`horas_transcurridas` / `lead_time_horas`), nunca almacenado.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from modules.caja.schemas import OrigenFondos
from modules.proveedores.pagos import PartePago

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
    """Línea del pedido: qué se le está comprando al proveedor, cuánto y a qué costo unitario.

    `costo_estimado` es el costo unitario ACORDADO al pedir; el real se confirma al recibir (y se
    puede corregir después). Los tres campos son obligatorios: sin ellos el pedido no sirve para
    saber cuánto se está comprometiendo ni para cuadrar el inventario al llegar.
    """

    producto_id: int = Field(gt=0)
    descripcion: str | None = Field(default=None, max_length=300)
    cantidad: Decimal = Field(gt=0)
    costo_estimado: Decimal = Field(ge=0, le=MAX_MONTO)


class PedidoCrear(BaseModel):
    proveedor: ProveedorRef
    descripcion: str | None = Field(default=None, max_length=500)
    monto_estimado: Decimal | None = Field(default=None, gt=0, le=MAX_MONTO)
    fecha_estimada: date | None = None
    lineas: list[LineaPedidoCrear] = Field(min_length=1, max_length=200)
    notas: str | None = Field(default=None, max_length=1000)
    # Cómo se paga, declarado AL PEDIR: `contado` (se paga todo ahora), `credito` (se paga después,
    # nace la cuenta por pagar al recibir) o `anticipado` (se entrega una parte ahora y el resto al
    # recibir). El servicio deriva el `anticipo` de `contado` y valida el parcial de `anticipado`.
    condicion_pago: CondicionPago
    anticipo: Decimal | None = Field(default=None, gt=0, le=MAX_MONTO)
    origen_fondos: OrigenFondos = "caja"
    # Pago MIXTO (0068): reparte lo que se paga AHORA entre medios (una parte del cajón, otra por
    # transferencia…). Vacío = todo por `origen_fondos`. La suma debe dar el monto que se paga.
    pagos: list[PartePago] = Field(default_factory=list)
    idempotency_key: str | None = None


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
    # Opcional: manda la condición declarada al pedir, pero se puede corregir aquí (la realidad
    # gana: "iba a ser crédito y terminé pagando de contado").
    condicion_pago: CondicionPago | None = None
    # Crédito: nº de factura del proveedor (PK de facturas_proveedores) y vencimiento opcionales.
    numero_factura: str | None = Field(default=None, min_length=1, max_length=100)
    fecha_vencimiento: date | None = None
    # Contado: si el pago sale AHORA y de dónde. `caja` exige caja abierta y postea el egreso.
    pago_ahora: bool = False
    origen_fondos: OrigenFondos = "caja"
    pagos: list[PartePago] = Field(default_factory=list)   # pago mixto (0068)
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
    origen_anticipo: str | None = None
    origen_pago: str | None = None
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
