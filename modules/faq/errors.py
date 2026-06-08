"""Errores de dominio del pack FAQ / conocimiento (el router los mapea a HTTP)."""


class FaqError(Exception):
    """Base de los errores del pack FAQ."""


class ConocimientoInexistente(FaqError):
    def __init__(self, conocimiento_id: int) -> None:
        super().__init__(f"La entrada de conocimiento {conocimiento_id} no existe")
        self.conocimiento_id = conocimiento_id
