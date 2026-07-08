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


class ObraSinCotizacion(ObrasError):
    """No se puede facturar una obra sin una cotización GANADA de la que sacar los ítems (Fase 7). 409.

    Cubre la obra "suelta" (`cotizacion_id` NULL), la cotización perdida/borrada y la que no está en
    estado GANADA: sin cotización ganada no hay nada legítimo que facturar.
    """

    def __init__(self, obra_id: int) -> None:
        super().__init__(
            f"La obra {obra_id} no tiene una cotización GANADA con ítems: no hay qué facturar"
        )
        self.obra_id = obra_id


class ObraSinCliente(ObrasError):
    """No se puede facturar una obra sin cliente (el documento DIAN necesita un adquirente). 409.

    Guarda defensiva: `obras.cliente_id` es NOT NULL en la base, pero la factura se emite A NOMBRE del
    cliente de la obra; sin él no se arma un adquirente válido (no se factura a consumidor final una obra).
    """

    def __init__(self, obra_id: int) -> None:
        super().__init__(f"La obra {obra_id} no tiene cliente: no se puede emitir la factura")
        self.obra_id = obra_id
