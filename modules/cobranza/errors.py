"""Errores de dominio del pack cobranza (los mapean las herramientas IA y el router)."""


class ClienteNoIdentificado(Exception):
    """El teléfono que escribe no corresponde a ningún cliente registrado del negocio."""


class SinDeuda(Exception):
    """El cliente no tiene saldo pendiente (no aplica prometer/reportar pago)."""


class FechaPromesaInvalida(Exception):
    """La fecha prometida no es válida (pasada o más allá del tope permitido)."""


class PagoReportadoInexistente(Exception):
    """El pago reportado no existe (dashboard)."""
