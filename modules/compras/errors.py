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
