"""Errores de dominio del pack pagar (los mapeará el router del dashboard en Fase 2)."""


class ConfigPagarInvalida(Exception):
    """La configuración de avisos no es válida (límites fuera de rango)."""


class FacturaInexistente(Exception):
    """La factura de proveedor referida no existe (dashboard)."""
