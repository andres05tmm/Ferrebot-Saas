"""Errores del motor contable (ADR 0030). Los mapea a HTTP el router."""
from __future__ import annotations


class ContabilidadError(Exception):
    """Base del dominio contable."""


class AsientoDescuadrado(ContabilidadError):
    """Débitos ≠ créditos: un asiento descuadrado JAMÁS se puede postear (invariante)."""


class AsientoInmutable(ContabilidadError):
    """Un asiento `posted` no se edita: la corrección es un asiento espejo (reversar)."""


class PeriodoBloqueado(ContabilidadError):
    """El período (locked/closed) rechaza el posting."""


class CuentaInexistente(ContabilidadError):
    """No hay cuenta PUC con ese código."""


class CuentaNoImputable(ContabilidadError):
    """Solo las hojas (`imputable`) reciben movimientos; una de agrupación no."""


class AsientoConflicto(ContabilidadError):
    """Misma `idempotency_key` con un asiento ya existente y payload incompatible."""


class ProyeccionInvalida(ContabilidadError):
    """El evento origen no existe o no es proyectable (p. ej. venta anulada)."""
