"""Errores de dominio de devoluciones (manejo explícito; el router los mapea a HTTP)."""


class DevolucionError(Exception):
    """Base de errores de devolución."""


class VentaNoEncontrada(DevolucionError):
    """La venta a devolver no existe (→ 404)."""

    def __init__(self, venta_id: int) -> None:
        super().__init__(f"La venta {venta_id} no existe")
        self.venta_id = venta_id


class LineaNoVendida(DevolucionError):
    """Se intenta devolver un producto que la venta no incluye (→ 422)."""

    def __init__(self, producto_id: int) -> None:
        super().__init__(f"El producto {producto_id} no está en la venta")
        self.producto_id = producto_id


class DevolucionExcedeVenta(DevolucionError):
    """La cantidad a devolver (esta solicitud + devoluciones previas) excede lo vendido (→ 409).

    Sin este guard, dos devoluciones con keys distintas podrían re-ingresar más stock del vendido y
    reintegrar el dinero dos veces. El acumulado por producto se valida contra `devoluciones_detalle`."""

    def __init__(self, producto_id: int) -> None:
        super().__init__(
            f"La cantidad a devolver del producto {producto_id} excede lo vendido "
            "(contando devoluciones previas)"
        )
        self.producto_id = producto_id


class NadaPorDevolver(DevolucionError):
    """La venta ya fue devuelta por completo: no queda cantidad por reintegrar (→ 409)."""

    def __init__(self, venta_id: int) -> None:
        super().__init__(f"La venta {venta_id} ya no tiene nada por devolver")
        self.venta_id = venta_id


class DevolucionConflicto(DevolucionError):
    """Misma `idempotency_key` reusada con un payload distinto (venta/líneas/método) (→ 409).

    Cierra FF-1: la key ya identifica otra devolución; no se reintenta ni se duplica.
    """

    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency_key '{key}' ya usada con un payload distinto")
        self.key = key


class CajaRequerida(DevolucionError):
    """Un reintegro en efectivo exige una caja abierta para el usuario (→ 409)."""

    def __init__(self, usuario_id: int) -> None:
        super().__init__("El reintegro en efectivo requiere una caja abierta")
        self.usuario_id = usuario_id


class FiadoNoEncontrado(DevolucionError):
    """La venta a crédito no tiene un fiado asociado que abonar (→ 409)."""

    def __init__(self, venta_id: int) -> None:
        super().__init__(f"La venta {venta_id} no tiene un fiado asociado para reintegrar")
        self.venta_id = venta_id
