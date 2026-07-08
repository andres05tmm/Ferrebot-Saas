"""Errores de dominio de herramientas (el router los mapea a HTTP).

Calca `modules/maquinaria/errors.py`: excepciones de dominio que el router traduce a 404 / 409.
"""


class HerramientasError(Exception):
    """Base de errores de herramientas."""


class HerramientaInexistente(HerramientasError):
    def __init__(self, herramienta_id: int) -> None:
        super().__init__(f"La herramienta {herramienta_id} no existe")
        self.herramienta_id = herramienta_id


class CodigoHerramientaDuplicado(HerramientasError):
    """Otra herramienta ya usa ese código (columna UNIQUE) → 409."""

    def __init__(self, codigo: str) -> None:
        super().__init__(f"Ya existe una herramienta con el código {codigo!r}")
        self.codigo = codigo
