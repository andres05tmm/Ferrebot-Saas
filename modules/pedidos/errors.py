"""Errores de dominio del pack pedidos (los mapean las herramientas IA y el router)."""


class CocinaCerrada(Exception):
    """Fuera del horario de cocina (o pedidos desactivados): no se arman pedidos."""


class ProductoNoEncontrado(Exception):
    """Un ítem no resolvió contra el catálogo. `sugerencias` = candidatos del buscador."""

    def __init__(self, nombre: str, sugerencias: list[str]) -> None:
        super().__init__(nombre)
        self.nombre = nombre
        self.sugerencias = sugerencias


class StockInsuficiente(Exception):
    """No hay inventario suficiente para la cantidad pedida."""

    def __init__(self, nombre: str, disponible) -> None:
        super().__init__(nombre)
        self.nombre = nombre
        self.disponible = disponible


class SinBorrador(Exception):
    """No hay pedido en armado (`recibido`) para confirmar."""


class PedidoMuyChico(Exception):
    """El subtotal no alcanza el mínimo de pedido del negocio."""

    def __init__(self, minimo) -> None:
        super().__init__(str(minimo))
        self.minimo = minimo


class ModificadorNoEncontrado(Exception):
    """Un modificador no resolvió contra las opciones del producto (anti-alucinación: preguntar).

    `sugerencias` = opciones activas disponibles del producto (para que el bot ofrezca, no invente).
    """

    def __init__(self, nombre: str, producto: str, sugerencias: list[str]) -> None:
        super().__init__(nombre)
        self.nombre = nombre
        self.producto = producto
        self.sugerencias = sugerencias


class ModificadorInvalido(Exception):
    """Selección fuera de las reglas del grupo (obligatorio sin elegir, o excede el máximo)."""

    def __init__(self, producto: str, mensaje: str, opciones: list[str] | None = None) -> None:
        super().__init__(mensaje)
        self.producto = producto
        self.mensaje = mensaje
        self.opciones = opciones or []


class PedidoInexistente(Exception):
    """El pedido no existe (dashboard)."""


class TransicionInvalida(Exception):
    """Cambio de estado fuera del ciclo permitido."""

    def __init__(self, actual: str, nuevo: str) -> None:
        super().__init__(f"{actual} → {nuevo}")
        self.actual = actual
        self.nuevo = nuevo
