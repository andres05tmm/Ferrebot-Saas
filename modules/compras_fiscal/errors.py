"""Errores de dominio de compras fiscales (el router los mapea a HTTP)."""


class ComprasFiscalError(Exception):
    """Base de errores de compras fiscales."""


class CompraInexistente(ComprasFiscalError):
    """La compra normal de la que se quiere derivar la fiscal no existe (→ 404)."""

    def __init__(self, compra_id: int) -> None:
        super().__init__(f"La compra {compra_id} no existe")
        self.compra_id = compra_id


class CompraFiscalInexistente(ComprasFiscalError):
    """La compra fiscal sobre la que se quiere enviar un evento RADIAN no existe (→ 404)."""

    def __init__(self, fiscal_id: int) -> None:
        super().__init__(f"La compra fiscal {fiscal_id} no existe")
        self.fiscal_id = fiscal_id


class CufeNoImportado(ComprasFiscalError):
    """No se puede aceptar/reclamar una FE recibida sin haber importado antes su CUFE (→ 409)."""

    def __init__(self, fiscal_id: int) -> None:
        super().__init__(f"La compra fiscal {fiscal_id} no tiene CUFE importado: impórtalo primero")
        self.fiscal_id = fiscal_id
