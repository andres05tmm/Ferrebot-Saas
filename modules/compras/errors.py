"""Errores de dominio de compras (manejo explícito; el router los mapea a HTTP)."""


class ComprasError(Exception):
    """Base de errores de compras."""


class IdempotenciaConflicto(ComprasError):
    """Misma `idempotency_key` reusada con un payload distinto (ai-tools.md §4, código

    `idempotencia_conflicto`, no recuperable). No se reintenta ni se duplica: la key ya identifica
    otra compra. El router lo mapea a 409.
    """

    def __init__(self, key: str) -> None:
        super().__init__(f"La idempotency_key «{key}» ya existe con un payload distinto")
        self.key = key


class CompraInexistente(ComprasError):
    def __init__(self, compra_id: int) -> None:
        super().__init__(f"La compra {compra_id} no existe")
        self.compra_id = compra_id


class CompraNoCorregible(ComprasError):
    """La compra no admite corrección por diferencia (p. ej. imputada a obra: nunca movió stock,
    o ya tiene retenciones practicadas: se corrige por nota de ajuste fiscal)."""

    def __init__(self, compra_id: int, motivo: str) -> None:
        super().__init__(f"La compra {compra_id} no se puede corregir: {motivo}")
        self.compra_id = compra_id
        self.motivo = motivo


class CorreccionInvalida(ComprasError):
    """La corrección rompe una regla de dominio (dejaría la factura sobrepagada, etc.)."""
