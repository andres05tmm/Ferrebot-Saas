"""Errores de dominio de maquinaria (el router los mapea a HTTP).

Calca `modules/inventario/errors.py`: excepciones de dominio propias que el router traduce a
código HTTP (404 / 409). Así el servicio no conoce FastAPI y se puede testear aislado.
"""


class MaquinariaError(Exception):
    """Base de errores de maquinaria."""


class MaquinaInexistente(MaquinariaError):
    def __init__(self, maquina_id: int) -> None:
        super().__init__(f"La máquina {maquina_id} no existe")
        self.maquina_id = maquina_id


class CodigoMaquinaDuplicado(MaquinariaError):
    """Otra máquina ya usa ese código (columna UNIQUE) → 409."""

    def __init__(self, codigo: str) -> None:
        super().__init__(f"Ya existe una máquina con el código {codigo!r}")
        self.codigo = codigo
