"""Roles y jerarquía (auth-rbac.md, SECURITY.md).

super_admin (operador SaaS) > admin (empresa) > vendedor. La comparación de capacidad
usa el rango; un rol satisface un requisito si su rango es >= al requerido.
"""
from enum import IntEnum


class Rol(IntEnum):
    vendedor = 1
    admin = 2
    super_admin = 3


def rank(rol: str) -> int:
    try:
        return int(Rol[rol])
    except KeyError:
        return 0


def satisface(rol_usuario: str, rol_requerido: str) -> bool:
    return rank(rol_usuario) >= rank(rol_requerido)
