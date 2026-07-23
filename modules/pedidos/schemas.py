"""Schemas Pydantic del pack pedidos (dashboard + motor). Validación de toda entrada (security.md)."""
from datetime import datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PedidoConfigLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    activo: bool
    hora_apertura: time
    hora_cierre: time
    minimo_pedido: Decimal
    tiempo_estimado_min: int
    costo_domicilio_default: Decimal


class PedidoConfigActualizar(BaseModel):
    activo: bool = True
    hora_apertura: time = time(8, 0)
    hora_cierre: time = time(21, 0)
    minimo_pedido: Decimal = Field(default=Decimal("0"), ge=0)
    tiempo_estimado_min: int = Field(default=45, ge=5, le=240)
    costo_domicilio_default: Decimal = Field(default=Decimal("0"), ge=0)


class ZonaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    tarifa: Decimal
    activo: bool


class ZonaCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    tarifa: Decimal = Field(ge=0)
    activo: bool = True


class PedidoItemLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    producto_id: int | None
    nombre: str
    cantidad: Decimal
    precio_unitario: Decimal
    subtotal: Decimal
    # Snapshot de modificadores (F2): [{grupo, opcion, delta_precio}] o None.
    modificadores: list[dict] | None = None


class PedidoLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cliente_nombre: str | None
    cliente_telefono: str
    telefono_contacto: str | None = None
    direccion: str | None
    zona_id: int | None
    costo_domicilio: Decimal
    metodo_pago: str | None
    estado: str
    subtotal: Decimal
    total: Decimal
    notas: str | None
    origen: str
    creado_en: datetime
    actualizado_en: datetime
    # Venta vinculada por la conversión (F1 / ADR 0032); None = aún no convertido.
    venta_id: int | None = None
    items: list[PedidoItemLeer]
    # ¿Tiene un cobro `pagado` por (origen="pedido", origen_id=id)? Lo anota el repositorio al
    # listar (atributo transitorio); en respuestas de un solo pedido cae al default seguro.
    pagado: bool = False


class CambioEstado(BaseModel):
    estado: str = Field(min_length=1)


class MesaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre: str
    zona: str | None
    activo: bool
    # Orden abierta anotada por el router (None = mesa libre).
    pedido_id: int | None = None
    total: Decimal | None = None


class MesaCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    zona: str | None = Field(default=None, max_length=80)


class ItemMesa(BaseModel):
    producto: str = Field(min_length=1, max_length=120)
    cantidad: Decimal = Field(gt=0, le=999)
    modificadores: list[str] = Field(default_factory=list, max_length=10)


class RondaMesa(BaseModel):
    items: list[ItemMesa] = Field(min_length=1, max_length=30)


class CobrarMesa(BaseModel):
    metodo_pago: str = Field(min_length=1, max_length=40)
    # Propina opcional elegida por el cliente (solo salón — ADR 0032 D7).
    propina: Decimal | None = Field(default=None, ge=0)


class ComandaItemLeer(BaseModel):
    nombre: str
    cantidad: Decimal
    modificadores: list[dict] | None = None


class ComandaLeer(BaseModel):
    id: int
    pedido_id: int
    zona_id: int | None
    zona: str | None = None
    estado: str
    creada_en: datetime
    items: list[ComandaItemLeer]


class KdsLeer(BaseModel):
    zonas: list[dict]
    comandas: list[ComandaLeer]


class ZonaComandaCrear(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)


class RuteoComanda(BaseModel):
    producto_id: int
    zona_id: int | None = None


class CambioEstadoComanda(BaseModel):
    estado: str = Field(min_length=1)


class ConvertirPayload(BaseModel):
    """Conversión pedido → venta (F1 / ADR 0032). `metodo_pago` explícito gana sobre el del pedido."""

    metodo_pago: str | None = None


class ConversionLeer(BaseModel):
    venta_id: int
    total: Decimal
    replay: bool
