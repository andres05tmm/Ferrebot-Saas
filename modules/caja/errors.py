"""Errores de dominio de caja/gastos (el router los mapea a HTTP)."""


class CajaError(Exception):
    """Base de errores de caja."""


class CajaNoAbierta(CajaError):
    """No hay caja abierta para el vendedor (no se puede mover caja ni registrar gasto)."""

    def __init__(self, usuario_id: int) -> None:
        super().__init__(f"El vendedor {usuario_id} no tiene una caja abierta")
        self.usuario_id = usuario_id
