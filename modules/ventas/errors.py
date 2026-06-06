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


# --- Borrado de venta (DELETE /ventas/{id}) ------------------------------------------------------
class VentaNoEncontrada(VentaError):
    """La venta a borrar no existe (→ 404)."""

    def __init__(self, venta_id: int) -> None:
        super().__init__(f"La venta {venta_id} no existe")
        self.venta_id = venta_id


class VentaNoEsDeHoy(VentaError):
    """Solo se pueden borrar ventas del día en curso (hora Colombia) (→ 409)."""

    def __init__(self, venta_id: int) -> None:
        super().__init__("Solo se pueden borrar ventas del día")
        self.venta_id = venta_id


class BorradoNoAutorizado(VentaError):
    """Un vendedor intenta borrar una venta que no es suya (→ 403). El admin puede cualquiera."""

    def __init__(self, venta_id: int) -> None:
        super().__init__("No puedes borrar una venta de otro vendedor")
        self.venta_id = venta_id


class VentaConFacturaViva(VentaError):
    """La venta tiene una factura electrónica viva (pendiente/aceptada); no se puede borrar (→ 409)."""

    def __init__(self, venta_id: int) -> None:
        super().__init__("La venta tiene factura electrónica; no se puede borrar")
        self.venta_id = venta_id
