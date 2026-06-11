"""Errores de dominio del pack ventas/cotizaciones."""


class ProductoNoResuelto(Exception):
    """El texto no resolvió contra el catálogo. `sugerencias` = candidatos del buscador."""

    def __init__(self, texto: str, sugerencias: list[str]) -> None:
        super().__init__(texto)
        self.texto = texto
        self.sugerencias = sugerencias


class CarritoVacio(Exception):
    """No hay cotización abierta (o está sin ítems) para la operación."""


class CotizacionInexistente(Exception):
    """La cotización no existe (dashboard)."""


class EstadoInvalido(Exception):
    """Marcado fuera del ciclo (p. ej. aceptar una cancelada)."""

    def __init__(self, actual: str, nuevo: str) -> None:
        super().__init__(f"{actual} → {nuevo}")
        self.actual = actual
        self.nuevo = nuevo
