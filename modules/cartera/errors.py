"""Errores de dominio de la cartera de alquiler (el router los mapea a HTTP).

Calca `modules/maquinaria/errors.py`: excepciones propias que el router traduce a código HTTP, para
que el servicio no conozca FastAPI y se pueda testear aislado.
"""


class CarteraError(Exception):
    """Base de errores de la cartera de alquiler."""


class CupoInexistente(CarteraError):
    """No existe un cupo con ese id → 404."""

    def __init__(self, cupo_id: int) -> None:
        super().__init__(f"El cupo de alquiler {cupo_id} no existe")
        self.cupo_id = cupo_id
