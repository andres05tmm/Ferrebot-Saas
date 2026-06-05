"""Errores de dominio de compras fiscales (el router los mapea a HTTP)."""


class ComprasFiscalError(Exception):
    """Base de errores de compras fiscales."""


class CompraInexistente(ComprasFiscalError):
    """La compra normal de la que se quiere derivar la fiscal no existe (→ 404)."""

    def __init__(self, compra_id: int) -> None:
        super().__init__(f"La compra {compra_id} no existe")
        self.compra_id = compra_id
