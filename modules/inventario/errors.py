"""Errores de dominio de inventario (el router los mapea a HTTP)."""


class InventarioError(Exception):
    """Base de errores de inventario."""


class ProductoInexistente(InventarioError):
    def __init__(self, producto_id: int) -> None:
        super().__init__(f"Producto {producto_id} no existe")
        self.producto_id = producto_id


class CodigoDuplicado(InventarioError):
    """Otro producto ya usa ese código (columna UNIQUE)."""

    def __init__(self, codigo: str) -> None:
        super().__init__(f"Ya existe un producto con el código {codigo!r}")
        self.codigo = codigo


class AjusteDejaStockNegativo(InventarioError):
    def __init__(self, producto_id: int, actual, delta) -> None:
        super().__init__(
            f"El ajuste deja stock negativo en producto {producto_id}: "
            f"actual {actual}, delta {delta}"
        )
        self.producto_id = producto_id
        self.actual = actual
        self.delta = delta
