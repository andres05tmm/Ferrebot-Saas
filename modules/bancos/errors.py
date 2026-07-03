"""Errores de dominio de conciliación bancaria (el router los mapea a HTTP)."""


class BancosError(Exception):
    """Base de errores de conciliación bancaria."""


class MovimientoBancarioInexistente(BancosError):
    def __init__(self, mov_id: int) -> None:
        super().__init__(f"El movimiento bancario {mov_id} no existe")
        self.mov_id = mov_id


class ConciliacionInvalida(BancosError):
    """El enlace propuesto no calza con ningún candidato interno (monto/fecha/naturaleza)."""
