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


# --- Modificación de venta (borrar/editar) -------------------------------------------------------
# Guards compartidos por DELETE y PUT /ventas/{id}; el verbo `accion` ("borrar"/"editar") solo cambia
# el texto del mensaje (mismas reglas: solo HOY, sin factura viva, admin o vendedor dueño).
class VentaNoEncontrada(VentaError):
    """La venta no existe (→ 404)."""

    def __init__(self, venta_id: int) -> None:
        super().__init__(f"La venta {venta_id} no existe")
        self.venta_id = venta_id


class VentaNoEsDeHoy(VentaError):
    """Solo se puede modificar una venta del día en curso (hora Colombia) (→ 409)."""

    def __init__(self, venta_id: int, *, accion: str = "borrar") -> None:
        super().__init__(f"Solo se pueden {accion} ventas del día")
        self.venta_id = venta_id


class OperacionNoAutorizada(VentaError):
    """Un vendedor intenta modificar una venta que no es suya (→ 403). El admin puede cualquiera."""

    def __init__(self, venta_id: int, *, accion: str = "borrar") -> None:
        super().__init__(f"No puedes {accion} una venta de otro vendedor")
        self.venta_id = venta_id


class VentaConFacturaViva(VentaError):
    """La venta tiene una factura electrónica viva (pendiente/aceptada); no se puede modificar (→ 409)."""

    def __init__(self, venta_id: int, *, accion: str = "borrar") -> None:
        super().__init__(f"La venta tiene factura electrónica; no se puede {accion}")
        self.venta_id = venta_id
