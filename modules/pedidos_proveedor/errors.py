"""Errores de dominio de pedidos a proveedor (mapeados a HTTP en el router)."""


class PedidoInexistente(Exception):
    def __init__(self, pedido_id: int) -> None:
        super().__init__(f"Pedido a proveedor {pedido_id} no existe")
        self.pedido_id = pedido_id


class PedidoNoEditable(Exception):
    """El pedido ya no está en estado `pedido` (recibido o cancelado): no se edita ni se re-procesa."""

    def __init__(self, pedido_id: int, estado: str) -> None:
        super().__init__(f"Pedido {pedido_id} está '{estado}': solo un pedido en camino se puede modificar")
        self.pedido_id = pedido_id
        self.estado = estado


class RecepcionInvalida(Exception):
    """La recepción no cumple una regla de dominio (sin líneas, factura duplicada, etc.)."""


class IdempotenciaConflicto(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"Idempotency-Key {key!r} ya fue usada con un payload distinto")
        self.key = key
