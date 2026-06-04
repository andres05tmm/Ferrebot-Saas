"""Errores de dominio de ventas (manejo explícito; el router los mapea a HTTP)."""


class VentaError(Exception):
    """Base de errores de venta."""


class LineaInvalida(VentaError):
    """Una línea no trae los datos mínimos (p. ej. venta varia sin precio/descripción)."""


class ProductoNoEncontrado(VentaError):
    def __init__(self, producto_id: int) -> None:
        super().__init__(f"Producto {producto_id} no existe o está inactivo")
        self.producto_id = producto_id


class StockInsuficiente(VentaError):
    def __init__(self, producto_id: int, disponible, solicitado) -> None:
        super().__init__(
            f"Stock insuficiente para producto {producto_id}: "
            f"disponible {disponible}, solicitado {solicitado}"
        )
        self.producto_id = producto_id
        self.disponible = disponible
        self.solicitado = solicitado
