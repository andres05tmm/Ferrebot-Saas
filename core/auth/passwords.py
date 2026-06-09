"""Hashing de contraseñas (login real, ADR 0009 §D3): argon2id vía argon2-cffi.

PURO (sin IO): `hash_password` produce el hash con sal y parámetros embebidos; `verify_password`
compara en tiempo (casi) constante y NUNCA lanza —un hash None/vacío/corrupto devuelve False—, para
que el llamador no tenga que distinguir "no hay hash" de "no coincide" (sin enumeración de usuarios).

Parámetros: los DEFAULTS de `PasswordHasher` (argon2id, time_cost=3, memory_cost=64 MiB, parallelism=4),
sensatos para login interactivo. Centralizado para no esparcir SHA plano por el código (regla #5).
"""
from __future__ import annotations

from argon2 import PasswordHasher

_hasher = PasswordHasher()  # argon2id con defaults de la librería


def hash_password(plano: str) -> str:
    """Hash argon2id (con sal aleatoria + parámetros embebidos en el string `$argon2id$...`)."""
    return _hasher.hash(plano)


def verify_password(plano: str, password_hash: str | None) -> bool:
    """True si `plano` corresponde a `password_hash`. NUNCA lanza: None/vacío/corrupto → False."""
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, plano)
    except Exception:  # noqa: BLE001 — mismatch/hash inválido/corrupto: jamás propagar, solo False
        return False
