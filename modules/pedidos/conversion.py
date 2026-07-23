"""Conversión pedido → venta (F1 Pack Restaurante, ADR 0032): el puente pedidos → contabilidad.

Cierra el "v2" declarado en el ADR 0016. Orquesta el repo de pedidos y el servicio de ventas SOBRE
LA MISMA SESIÓN (una sola transacción): toma el pedido con `FOR UPDATE`, arma la venta con el
SNAPSHOT del pedido (precio del pedido, no el del catálogo de hoy) e idempotency_key derivada, y
vincula `pedidos.venta_id` (UNIQUE) antes del commit — patrón calcado de `agenda/cobro.py`
(ADR 0022 D3). NO postea `caja_movimientos`: el arqueo híbrido cuadra por `ventas_efectivo`.

Líneas: producto ACTIVO del catálogo → línea de catálogo con precio override del snapshot (el stock
baja vía movimiento SALIDA en la venta — regla #7, único punto donde el pedido toca inventario);
producto borrado/desactivado → línea VARIA con el snapshot (no bloquea la conversión ni toca stock).
El domicilio, si lo hay, va como línea varia (iva=0).

Módulo aparte de `service.py` a propósito: la conversión es un caso de uso contable, no del ciclo
del pedido.
"""
from dataclasses import dataclass
from decimal import Decimal

from modules.pedidos.errors import PedidoInexistente
from modules.pedidos.repository import SqlPedidosRepository
from modules.ventas.schemas import VentaCrear, VentaDetalleCrear
from modules.ventas.service import VentaService

# Estados desde los que se convierte: el cliente ya confirmó. `entregado` entra SOLO si aún no tiene
# venta (se entregó primero, se registró después). `abierto` es el cobro de MESA (F3). `recibido`
# (borrador) y `cancelado`, jamás.
_ESTADOS_CONVERTIBLES = ("confirmado", "en_preparacion", "en_camino", "entregado", "abierto")

# Métodos que acepta el puente. `fiado` queda fuera (requiere cliente_id del POS; la identidad del
# pedido es el teléfono) — mismo recorte que el cobro de citas (ADR 0022 D6).
_METODOS = ("efectivo", "transferencia", "datafono")


class PedidoNoConvertible(Exception):
    """El pedido no admite conversión (estado, método de pago inválido, etc.)."""

    def __init__(self, pedido_id: int, motivo: str) -> None:
        super().__init__(f"El pedido {pedido_id} no se puede convertir: {motivo}")
        self.pedido_id = pedido_id
        self.motivo = motivo


@dataclass(frozen=True, slots=True)
class ResultadoConversion:
    venta_id: int
    total: Decimal
    replay: bool  # True = el pedido ya estaba convertido (o la venta ya existía): misma venta


def _idempotency_key(pedido_id: int) -> str:
    return f"pedido-venta:{pedido_id}"


async def convertir_pedido(
    pedido_id: int,
    *,
    repo: SqlPedidosRepository,
    ventas: VentaService,
    usuario_id: int,
    metodo_pago: str | None = None,
    propina: Decimal | None = None,
    control_stock_estricto: bool = False,
) -> ResultadoConversion:
    """Convierte el pedido en venta (o la reusa). Idempotente; ver ADR 0032 / ADR 0022.

    `repo` y `ventas` DEBEN compartir la sesión del tenant: la venta y el vínculo van en la misma
    transacción, y el `FOR UPDATE` del pedido serializa conversiones concurrentes.
    `metodo_pago` explícito (kanban) gana sobre el del pedido.
    """
    pedido = await repo.pedido_para_conversion(pedido_id)
    if pedido is None:
        raise PedidoInexistente(str(pedido_id))

    # Ya convertido: devolver la MISMA venta (replay). Reintentos de red inocuos.
    if pedido.venta_id is not None:
        venta = await ventas.obtener_venta(pedido.venta_id)
        if venta is not None:
            return ResultadoConversion(venta_id=venta.id, total=venta.total, replay=True)

    if pedido.estado not in _ESTADOS_CONVERTIBLES:
        raise PedidoNoConvertible(pedido_id, f"está '{pedido.estado}'")
    if not pedido.items:
        raise PedidoNoConvertible(pedido_id, "no tiene ítems")

    metodo = metodo_pago or pedido.metodo_pago
    if metodo not in _METODOS:
        raise PedidoNoConvertible(
            pedido_id, f"método de pago '{metodo}' inválido; envía metodo_pago"
        )
    # Propina SOLO en salón/mostrador, JAMÁS domicilio (decisión de Andrés, ADR 0032 D7).
    if propina is not None and propina > 0 and pedido.origen != "mesa":
        raise PedidoNoConvertible(pedido_id, "la propina solo aplica en salón (origen mesa)")

    lineas: list[VentaDetalleCrear] = []
    for item in pedido.items:
        activo = (
            item.producto_id is not None and await repo.producto_activo(item.producto_id)
        )
        # Los modificadores (F2) viajan en la DESCRIPCIÓN de la línea; su delta ya viene sumado en
        # el precio_unitario del snapshot del pedido.
        descripcion = item.nombre
        if item.modificadores:
            descripcion += " — " + ", ".join(m["opcion"] for m in item.modificadores)
        if activo:
            # Catálogo: el precio override es el SNAPSHOT del pedido; el stock baja vía SALIDA.
            lineas.append(
                VentaDetalleCrear(
                    producto_id=item.producto_id,
                    descripcion=descripcion,
                    cantidad=item.cantidad,
                    precio_unitario=item.precio_unitario,
                )
            )
        else:
            lineas.append(
                VentaDetalleCrear(
                    producto_id=None,
                    descripcion=descripcion,
                    cantidad=item.cantidad,
                    precio_unitario=item.precio_unitario,
                    iva=0,
                )
            )
    if pedido.costo_domicilio and pedido.costo_domicilio > 0:
        lineas.append(
            VentaDetalleCrear(
                producto_id=None,
                descripcion=f"Domicilio — pedido #{pedido.id}",
                cantidad=Decimal("1"),
                precio_unitario=pedido.costo_domicilio,
                iva=0,
            )
        )
    if propina is not None and propina > 0:
        # Línea varia discriminada (ADR 0022 D2): no toca stock por construcción, suma al total
        # (y por tanto a ventas_efectivo — la caja cuadra) sin alterar el total de productos.
        lineas.append(
            VentaDetalleCrear(
                producto_id=None,
                descripcion="Propina",
                cantidad=Decimal("1"),
                precio_unitario=propina,
                iva=0,
            )
        )

    datos = VentaCrear(
        metodo_pago=metodo,
        origen="web",
        idempotency_key=_idempotency_key(pedido.id),
        lineas=lineas,
    )
    # `registrar_venta` replaya por idempotency_key: si la venta ya existía (crash entre crear y
    # vincular, o carrera perdida tras el FOR UPDATE), devuelve la misma y aquí solo se re-vincula.
    resultado = await ventas.registrar_venta(
        datos, vendedor_id=usuario_id, control_stock_estricto=control_stock_estricto
    )
    await repo.vincular_venta(pedido, resultado.venta.id)
    return ResultadoConversion(
        venta_id=resultado.venta.id, total=resultado.venta.total, replay=resultado.replay
    )
