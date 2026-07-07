"""Errores de dominio de trabajadores (el router los mapea a HTTP)."""


class TrabajadoresError(Exception):
    """Base de errores de trabajadores."""


class TrabajadorInexistente(TrabajadoresError):
    def __init__(self, trabajador_id: int) -> None:
        super().__init__(f"El trabajador {trabajador_id} no existe")
        self.trabajador_id = trabajador_id


class TrabajadorDuplicado(TrabajadoresError):
    """Ya hay un trabajador con ese documento (la columna es UNIQUE en la base)."""

    def __init__(self, documento: str) -> None:
        super().__init__(f"Ya existe un trabajador con documento {documento!r}")
        self.documento = documento
