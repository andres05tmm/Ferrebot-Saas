"""Errores de dominio de caja/gastos (el router los mapea a HTTP)."""


class CajaError(Exception):
    """Base de errores de caja."""


class CajaNoAbierta(CajaError):
    """No hay caja abierta para el vendedor (no se puede mover caja ni registrar gasto)."""

    def __init__(self, usuario_id: int) -> None:
        super().__init__(f"El vendedor {usuario_id} no tiene una caja abierta")
        self.usuario_id = usuario_id


class GastoInexistente(CajaError):
    """No existe un gasto con ese id (p. ej. al aprobar uno de la bandeja de revisión). El router → 404."""

    def __init__(self, gasto_id: int) -> None:
        super().__init__(f"No existe el gasto {gasto_id}")
        self.gasto_id = gasto_id
