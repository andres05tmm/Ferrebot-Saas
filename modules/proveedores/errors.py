"""Errores de dominio de cuentas por pagar (el router los mapea a HTTP)."""


class ProveedoresError(Exception):
    """Base de errores de cuentas por pagar."""


class FacturaProveedorDuplicada(ProveedoresError):
    def __init__(self, factura_id: str) -> None:
        super().__init__(f"Ya existe una factura de proveedor con id {factura_id!r}")
        self.factura_id = factura_id


class FacturaProveedorInexistente(ProveedoresError):
    def __init__(self, factura_id: str) -> None:
        super().__init__(f"La factura de proveedor {factura_id!r} no existe")
        self.factura_id = factura_id


class AbonoInvalido(ProveedoresError):
    """El abono no es válido (p. ej. excede el pendiente de la factura)."""


class ProveedorInexistente(Exception):
    """No existe un proveedor con ese id (el estado de cuenta lo mapea a 404)."""

    def __init__(self, proveedor_id: int) -> None:
        super().__init__(f"El proveedor {proveedor_id} no existe")
        self.proveedor_id = proveedor_id
