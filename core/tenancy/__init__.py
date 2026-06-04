"""Resolución de empresa y enrutamiento de conexiones.

`TenantMiddleware` se importa desde core.tenancy.middleware donde se necesite (evita un ciclo
de importación con core.db.session).
"""
from core.tenancy.context import ResolvedTenant

__all__ = ["ResolvedTenant"]
