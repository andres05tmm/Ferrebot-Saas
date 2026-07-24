"""Tokens de dispositivo del agente de impresión (ADR 0033 D6) — plano de CONTROL.

El token opaco (`imp_<hex>`) se muestra UNA vez al emitirlo; en la base solo vive su sha256.
Solo autoriza la superficie `/api/v1/impresion` (la dependencia vive en el router del módulo).
"""
from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def emitir_dispositivo(cs: AsyncSession, empresa_id: int, nombre: str) -> tuple[int, str]:
    """Crea el dispositivo y devuelve (id, token_plano). El token no se puede recuperar después."""
    token = "imp_" + secrets.token_hex(24)
    dispositivo_id = (
        await cs.execute(
            text(
                "INSERT INTO dispositivos_impresion (empresa_id, nombre, token_hash) "
                "VALUES (:e, :n, :h) RETURNING id"
            ),
            {"e": empresa_id, "n": nombre, "h": _hash(token)},
        )
    ).scalar_one()
    return dispositivo_id, token


async def listar_dispositivos(cs: AsyncSession, empresa_id: int) -> list[dict]:
    filas = (
        await cs.execute(
            text(
                "SELECT id, nombre, activo, creado_en, revocado_en "
                "FROM dispositivos_impresion WHERE empresa_id = :e ORDER BY id"
            ),
            {"e": empresa_id},
        )
    ).all()
    return [dict(f._mapping) for f in filas]


async def revocar_dispositivo(cs: AsyncSession, empresa_id: int, dispositivo_id: int) -> bool:
    """Revoca (activo=false). Acotado a la empresa: jamás revoca dispositivos de otro tenant."""
    filas = (
        await cs.execute(
            text(
                "UPDATE dispositivos_impresion SET activo = false, revocado_en = now() "
                "WHERE id = :d AND empresa_id = :e RETURNING id"
            ),
            {"d": dispositivo_id, "e": empresa_id},
        )
    ).rowcount
    return bool(filas)


async def validar_token(cs: AsyncSession, empresa_id: int, token: str) -> int | None:
    """Id del dispositivo si el token es válido y ACTIVO para ESTA empresa; None si no."""
    return (
        await cs.execute(
            text(
                "SELECT id FROM dispositivos_impresion "
                "WHERE empresa_id = :e AND token_hash = :h AND activo"
            ),
            {"e": empresa_id, "h": _hash(token)},
        )
    ).scalar_one_or_none()
