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


class ObraNoFinalizada(ObrasError):
    """Liquidar exige que la obra esté FINALIZADA (el cierre del ciclo de vida). 409."""

    def __init__(self, obra_id: int, estado: str) -> None:
        super().__init__(
            f"La obra {obra_id} no se puede liquidar en estado {estado}: debe estar FINALIZADA"
        )
        self.obra_id = obra_id
        self.estado = estado


class ConsumoEnObraLiquidada(ObrasError):
    """No se imputan consumos a una obra ya LIQUIDADA (su snapshot está congelado). 409."""

    def __init__(self, obra_id: int) -> None:
        super().__init__(
            f"La obra {obra_id} está LIQUIDADA: su gasto real está congelado y no admite más consumos"
        )
        self.obra_id = obra_id
