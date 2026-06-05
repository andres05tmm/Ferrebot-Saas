"""Credenciales Cloudinary de una empresa, del control DB (espeja `cargar_config_matias`).

`api_key`/`api_secret` se descifran de `secretos_empresa`; `cloud_name` se lee en claro de
`config_empresa`. Si la empresa NO tiene Cloudinary configurado (falta alguna clave) → None: las
fotos de soporte quedan deshabilitadas sin romper el resto de cuentas por pagar.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt_split


@dataclass(frozen=True, slots=True)
class CloudinaryCredenciales:
    """Credenciales Cloudinary de UNA empresa (descifradas en memoria; nunca en código/git)."""

    cloud_name: str
    api_key: str
    api_secret: str


async def _secreto(session: AsyncSession, master: str, empresa_id: int, clave: str) -> str | None:
    """Lee y descifra un secreto de `secretos_empresa` (mismo patrón que facturacion.config)."""
    row = (
        await session.execute(
            text("SELECT valor_cifrado, nonce FROM secretos_empresa WHERE empresa_id = :e AND clave = :c"),
            {"e": empresa_id, "c": clave},
        )
    ).first()
    return decrypt_split(bytes(row[0]), bytes(row[1]), master) if row is not None else None


async def cargar_config_cloudinary(
    session: AsyncSession, master: str, empresa_id: int
) -> CloudinaryCredenciales | None:
    """Descifra las credenciales Cloudinary de la empresa, o None si no están completas. SQL solo aquí."""
    cloud_name = (
        await session.execute(
            text("SELECT valor FROM config_empresa WHERE empresa_id = :e AND clave = 'cloudinary_cloud_name'"),
            {"e": empresa_id},
        )
    ).scalar_one_or_none()
    api_key = await _secreto(session, master, empresa_id, "cloudinary_api_key")
    api_secret = await _secreto(session, master, empresa_id, "cloudinary_api_secret")
    if not (cloud_name and api_key and api_secret):
        return None
    return CloudinaryCredenciales(cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)
