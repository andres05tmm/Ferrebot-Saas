"""Errores de dominio del cotizador AIU (el router los mapea a HTTP).

Espeja `modules.obra.errors`: una jerarquía chica con un error base y subtipos que el router
traduce a 404/409. La lógica de negocio (existencia, ciclo de vida de estados, editabilidad,
conversión) los lanza; el HTTP vive solo en el router.
"""


class CotizacionObraError(Exception):
    """Base de errores del cotizador de obra."""


class CotizacionInexistente(CotizacionObraError):
    """La cotización no existe (→ 404)."""

    def __init__(self, cotizacion_id: int) -> None:
        super().__init__(f"La cotización de obra {cotizacion_id} no existe")
        self.cotizacion_id = cotizacion_id


class TransicionEstadoInvalida(CotizacionObraError):
    """Salto de estado no permitido por el ciclo de vida (→ 409)."""

    def __init__(self, actual: str, destino: str) -> None:
        super().__init__(f"Transición de estado inválida: {actual} → {destino}")
        self.actual = actual
        self.destino = destino


class CotizacionNoEditable(CotizacionObraError):
    """La cotización ya no admite edición del builder en su estado actual (→ 409).

    Editar sólo tiene sentido mientras la cotización está viva (BORRADOR/ENVIADA); una GANADA
    (ya convertible a obra), PERDIDA o VENCIDA es un documento cerrado: no se le tocan ítems ni AIU.
    """

    def __init__(self, estado: str) -> None:
        super().__init__(f"La cotización en estado {estado} no se puede editar")
        self.estado = estado


class CotizacionNoGanada(CotizacionObraError):
    """Se intentó convertir a obra una cotización que no está GANADA (→ 409)."""

    def __init__(self, estado: str) -> None:
        super().__init__(
            f"Sólo una cotización GANADA se convierte a obra (estado actual: {estado})"
        )
        self.estado = estado


class NumeroDuplicado(CotizacionObraError):
    """El número de cotización ya existe en la empresa (UNIQUE `numero`) (→ 409)."""

    def __init__(self, numero: str) -> None:
        super().__init__(f"Ya existe una cotización con el número {numero}")
        self.numero = numero
