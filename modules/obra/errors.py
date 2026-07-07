"""Errores de dominio de obras (el router los mapea a HTTP)."""


class ObrasError(Exception):
    """Base de errores de obras."""


class ObraInexistente(ObrasError):
    def __init__(self, obra_id: int) -> None:
        super().__init__(f"La obra {obra_id} no existe")
        self.obra_id = obra_id


class TransicionEstadoInvalida(ObrasError):
    """No se puede pasar de un estado a otro (transición no permitida por el ciclo de vida)."""

    def __init__(self, actual: str, destino: str) -> None:
        super().__init__(
            f"Transición de estado inválida: {actual} → {destino}"
        )
        self.actual = actual
        self.destino = destino
