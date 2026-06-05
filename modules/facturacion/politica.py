"""Política de estado/reintento de la emisión (PURA: sin red/BD).

Traduce la `categoria` de un `EmisionResultado` (E2) en una `Decision`: qué estado persistir, si
reintentar y si mandar a dead-letter. La consume el worker de E4b; aquí solo vive la regla pura.

RED (E4a): `decidir_emision` lanza `NotImplementedError`; el shape (Decision + firma) es definitivo.
"""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Decision:
    """Desenlace de la política: estado a persistir, si se reintenta y si va a dead-letter."""

    estado: str
    reintentar: bool
    dead_letter: bool


def decidir_emision(categoria: str, *, intentos: int, max_intentos: int) -> Decision:
    """Decide estado/reintento/dead-letter según la `categoria` del resultado de MATIAS.

    `intentos` = nº de fallos ya registrados (incluido el actual). Contrato (GREEN):
      - "aceptada"  → Decision("aceptada", reintentar=False, dead_letter=False).
      - "rechazada" → Decision("rechazada", reintentar=False, dead_letter=False)  # terminal, no-retry.
      - "error"     → estado="error"; reintentar = intentos < max_intentos; dead_letter = not reintentar.
    """
    if categoria in ("aceptada", "rechazada"):
        return Decision(categoria, reintentar=False, dead_letter=False)
    reintentar = intentos < max_intentos
    return Decision("error", reintentar=reintentar, dead_letter=not reintentar)
