"""Contratos Pydantic de ventas (entrada validada, salida del API)."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.money import cuantizar as _money
from modules.facturacion.repository import EstadoFiscalVenta

# Métodos vigentes para ventas NUEVAS (cierra #9). Las ventas históricas con tarjeta/nequi/daviplata
# se siguen leyendo (VentaLeer.metodo_pago es str; el enum de Postgres conserva esos valores).
MetodoPago = Literal["efectivo", "transferencia", "datafono", "fiado", "mixto"]
# Métodos que pueden ser PARTE de un cobro mixto (F5/0053): dinero que entra YA. `fiado` queda fuera
# (v1): el crédito tiene su propio ledger y no es plata en el cajón ni en el banco.
MetodoPagoParte = Literal["efectivo", "transferencia", "datafono"]
Origen = Literal["web", "bot", "voz", "offline"]


class PagoParte(BaseModel):
    """Una parte del cobro de una venta mixta: método + monto. La suma de las partes debe igualar
    el total de la venta (eso lo valida el servicio, que es quien conoce el total calculado)."""

    metodo: MetodoPagoParte
    monto: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _cuantizar_monto(self) -> "PagoParte":
        # A centavos DESDE la entrada: lo que valida el servicio (suma == total) es EXACTAMENTE lo
        # que persiste NUMERIC(12,2) — una parte con 3 decimales redondeada al insertar rompería
        # el invariante en silencio.
        self.monto = _money(self.monto)
        return self


class VentaDetalleCrear(BaseModel):
    producto_id: int | None = None
    descripcion: str | None = None
    cantidad: Decimal = Field(gt=0)
    # Catálogo: opcional (override de precio declarado). Varia: obligatorio.
    precio_unitario: Decimal | None = Field(default=None, ge=0)
    iva: int | None = Field(default=None, ge=0, le=100)
    # Tipo del impuesto de la tarifa `iva` (ADR 0032 D2). Solo lo usan las líneas VARIA (el
    # catálogo lo trae del producto): 'iva' (default histórico) o 'inc' (impoconsumo 8%).
    tipo_impuesto: str = Field(default="iva", pattern="^(iva|inc)$")
    # Cobrar la línea a precio de EMPAQUE (0072): el bulto de cemento a $28.000 en vez de 50 kg
    # sueltos a $1.500. `cantidad` sigue en la unidad de venta (50, no 1) — es la única unidad que
    # ven el inventario, el kárdex y la factura; esto solo cambia el precio.
    por_empaque: bool = False

    @model_validator(mode="after")
    def _validar_linea(self) -> "VentaDetalleCrear":
        if self.producto_id is None and (self.precio_unitario is None or not self.descripcion):
            raise ValueError("Una venta varia (sin producto_id) requiere descripcion y precio_unitario")
        return self


class VentaCrear(BaseModel):
    metodo_pago: MetodoPago
    cliente_id: int | None = None
    origen: Origen = "web"
    idempotency_key: str | None = None
    # Intención de documento fiscal por venta (ADR 0014): None → default por capacidad del tenant.
    # TRANSIENTE: no se persiste; solo rutea el cierre fiscal (`_resolver_documento` decide el efectivo
    # y cae al default si la intención no calza la capacidad). "No registrar ante DIAN" NO es opción aquí.
    documento: Literal["pos", "fe"] | None = None
    lineas: list[VentaDetalleCrear] = Field(min_length=1)
    # Partes del cobro de una venta MIXTA (F5/0053). Solo válidas con metodo_pago='mixto' (y ahí son
    # obligatorias: mínimo 2 — con una sola parte la venta ES de ese método, no mixta).
    pagos: list[PagoParte] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validar_pagos(self) -> "VentaCrear":
        if self.metodo_pago == "mixto" and len(self.pagos) < 2:
            raise ValueError("Una venta mixta requiere al menos 2 partes en `pagos`")
        if self.metodo_pago != "mixto" and self.pagos:
            raise ValueError("`pagos` solo aplica a ventas con metodo_pago='mixto'")
        return self


class VentaLeer(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    consecutivo: int
    cliente_id: int | None
    vendedor_id: int
    fecha: datetime
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    metodo_pago: str
    estado: str
    origen: str
    idempotency_key: str | None
    # Estado fiscal (badge): lo COMPONE el router solo si el tenant tiene capacidad fiscal; None si no
    # tiene capacidad o la venta no generó documento. Reusa el schema del módulo facturación (no se duplica).
    fiscal: EstadoFiscalVenta | None = None


class VentaDetalleLeer(BaseModel):
    """Línea de una venta (detalle). Solo lectura, para el detalle del historial."""

    model_config = ConfigDict(from_attributes=True)

    producto_id: int | None
    descripcion: str | None
    cantidad: Decimal
    precio_unitario: Decimal
    iva: int


class VentaConLineas(VentaLeer):
    """Detalle de venta: cabecera (VentaLeer) + sus líneas. La LISTA usa VentaLeer (sin líneas)."""

    lineas: list[VentaDetalleLeer]


class ItemVentaResumen(BaseModel):
    """Un renglón resumido para el feed de últimas ventas: nombre (catálogo o descripción) + cantidad."""

    nombre: str
    cantidad: Decimal


class VentaRecienteLeer(BaseModel):
    """Venta compacta para el feed 'Últimas ventas' del cockpit: cabecera mínima + sus items resueltos.

    `items` trae nombre+cantidad de cada renglón (nombre de catálogo, o la descripción de una venta varia);
    `num_items` es el conteo de renglones (el front muestra el primero + '+N' si hay más). Sin badge fiscal:
    el feed prioriza método de pago y producto, no el estado DIAN."""

    id: int
    consecutivo: int
    fecha: datetime
    total: Decimal
    metodo_pago: str
    items: list[ItemVentaResumen]
    num_items: int


class HistorialLinea(BaseModel):
    """Un RENGLÓN vendido del feed del tab Historial, con el contexto de su venta ya resuelto.

    Es el libro de ventas: una fila por producto vendido, no por venta. Las filas de una misma venta
    comparten `venta_id`/`consecutivo` — el front agrupa por ahí, porque editar y anular actúan sobre
    la VENTA entera (`PUT`/`DELETE /ventas/{venta_id}`), nunca sobre un renglón suelto.

    `total_linea` se reconstruye como `cantidad × precio_unitario`, igual que la factura DIAN. OJO:
    para fracciones y empaques el servicio guarda `precio_unitario = total / cantidad`
    (`modules/ventas/service.py:492`), así que la SUMA de los renglones puede diferir del total de la
    venta en centavos. Por eso viaja también `venta_total`: cualquier cifra que se sume sale de ahí,
    de la cabecera, jamás de las líneas.
    """

    linea_id: int
    venta_id: int
    consecutivo: int
    fecha: datetime                 # instante de la venta; el front la pinta en hora Colombia
    estado: str                     # 'completada' | 'anulada' — las anuladas se muestran atenuadas
    producto: str                   # nombre de catálogo, o la descripción si fue una venta varia
    producto_id: int | None         # None = venta varia (sin producto del catálogo)
    cantidad: Decimal
    precio_unitario: Decimal
    iva: int
    total_linea: Decimal
    cliente: str                    # 'Consumidor Final' cuando la venta no tiene cliente
    cliente_id: int | None
    vendedor: str
    vendedor_id: int | None
    metodo_pago: str                # 'mixto' NO es un método: es un marcador, el desglose va en `pagos`
    pagos: list[PagoParte] = []     # partes reales del cobro mixto; vacío en las ventas normales
    venta_total: Decimal            # total de la CABECERA: la única cifra que suma sin perder centavos
    num_lineas: int                 # renglones de esa venta, para el encabezado del grupo


class HistorialFeed(BaseModel):
    """Página del libro de ventas. Se pagina por VENTA, no por renglón: así un grupo nunca queda
    partido entre dos páginas y el front no tiene que reconstruir grupos a caballo.

    `hay_mas` sale de pedir un registro de más, no de un `COUNT(*)` sobre todo el rango.
    """

    desde: date
    hasta: date
    filas: list[HistorialLinea]
    hay_mas: bool
