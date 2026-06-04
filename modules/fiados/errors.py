"""Errores de dominio de fiados (el router los mapea a HTTP)."""


class FiadoError(Exception):
    """Base de errores de fiados."""


class ClienteInexistente(FiadoError):
    def __init__(self, cliente_id: int) -> None:
        super().__init__(f"Cliente {cliente_id} no existe")
        self.cliente_id = cliente_id


class FiadoInexistente(FiadoError):
    def __init__(self, fiado_id: int) -> None:
        super().__init__(f"Fiado {fiado_id} no existe")
        self.fiado_id = fiado_id


class SobreAbono(FiadoError):
    """El abono supera el saldo del fiado (no se permite saldo a favor)."""

    def __init__(self, fiado_id: int, saldo, monto) -> None:
        super().__init__(f"Abono {monto} supera el saldo {saldo} del fiado {fiado_id}")
        self.fiado_id = fiado_id
        self.saldo = saldo
        self.monto = monto
