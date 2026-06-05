"""E4a RED — política pura de estado/reintento de la emisión.

Pin del contrato: aceptada/rechazada son terminales (no-retry); error reintenta mientras
`intentos < max_intentos`, si no agota → dead-letter. En RED todos fallan por NotImplementedError.
"""
from modules.facturacion.politica import Decision, decidir_emision


def test_aceptada():
    assert decidir_emision("aceptada", intentos=0, max_intentos=5) == Decision("aceptada", False, False)


def test_rechazada():
    assert decidir_emision("rechazada", intentos=0, max_intentos=5) == Decision("rechazada", False, False)


def test_error_reintenta():
    d = decidir_emision("error", intentos=1, max_intentos=5)
    assert d.reintentar is True and d.dead_letter is False and d.estado == "error"


def test_error_dead_letter():
    d = decidir_emision("error", intentos=5, max_intentos=5)
    assert d.reintentar is False and d.dead_letter is True and d.estado == "error"
