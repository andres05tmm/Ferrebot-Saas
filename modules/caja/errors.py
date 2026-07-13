"""Errores de dominio de caja/gastos (el router los mapea a HTTP)."""


class CajaError(Exception):
    """Base de errores de caja."""


class CajaNoAbierta(CajaError):
    """No hay caja abierta para el vendedor (no se puede mover caja ni registrar gasto)."""

    def __init__(self, usuario_id: int) -> None:
        super().__init__(f"El vendedor {usuario_id} no tiene una caja abierta")
        self.usuario_id = usuario_id


class ObraNoImputable(CajaError):
    """El `obra_id` del gasto no admite imputación. `motivo` decide el mapeo HTTP del router:

    - `"inexistente"` (no existe o soft-deleted) → 404 (antes reventaba la FK con 500);
    - `"liquidada"` (snapshot inmutable: su gasto real quedó congelado) → 409.
    """

    def __init__(self, obra_id: int, motivo: str) -> None:
        super().__init__(f"La obra {obra_id} no admite imputar gastos ({motivo})")
        self.obra_id = obra_id
        self.motivo = motivo


class GastoNoPendiente(CajaError):
    """La acción solo aplica a gastos PENDIENTES de revisión → 409.

    Rechazar o re-imputar exige `requiere_revision = true` y no anulado: lo aprobado es definitivo
    (su egreso quedó asentado) y lo rechazado ya tiene su reversa. Cambiar el monto o el destino de un
    gasto definitivo = rechazarlo y registrar uno nuevo."""

    def __init__(self, gasto_id: int, accion: str) -> None:
        super().__init__(f"El gasto {gasto_id} no está pendiente de revisión: no se puede {accion}")
        self.gasto_id = gasto_id
        self.accion = accion


class GastoInexistente(CajaError):
    """No existe un gasto con ese id (p. ej. al aprobar uno de la bandeja de revisión). El router → 404."""

    def __init__(self, gasto_id: int) -> None:
        super().__init__(f"No existe el gasto {gasto_id}")
        self.gasto_id = gasto_id
