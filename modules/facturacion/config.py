"""Config de facturación de una empresa: credenciales MATIAS + parámetros DIAN, del control DB.

Vive en el dominio (no en una app): la consumen tanto el worker (`apps.worker`) como el endpoint
(`modules.facturacion.router`). SQL sobre la sesión de control que recibe (per-call); de
`secretos_empresa` descifra las credenciales y de `config_empresa` lee los parámetros no secretos.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt_split
from modules.facturacion.matias_client import MatiasCredenciales
from modules.facturacion.service import ConfigFiscal


async def _secreto(session: AsyncSession, master: str, empresa_id: int, clave: str) -> str | None:
    """Lee y descifra un secreto de `secretos_empresa` (patrón `ControlSecretosBot._leer`)."""
    row = (
        await session.execute(
            text("SELECT valor_cifrado, nonce FROM secretos_empresa WHERE empresa_id = :e AND clave = :c"),
            {"e": empresa_id, "c": clave},
        )
    ).first()
    return decrypt_split(bytes(row[0]), bytes(row[1]), master) if row is not None else None


async def _config(session: AsyncSession, empresa_id: int) -> dict[str, str]:
    """Lee `config_empresa` (clave→valor en claro) de una empresa."""
    rows = (
        await session.execute(
            text("SELECT clave, valor FROM config_empresa WHERE empresa_id = :e"),
            {"e": empresa_id},
        )
    ).all()
    return {r[0]: r[1] for r in rows}


async def cargar_config_matias(
    session: AsyncSession, master: str, empresa_id: int
) -> tuple[MatiasCredenciales, ConfigFiscal]:
    """Descifra credenciales + parámetros DIAN de una empresa desde el control DB. SQL solo aquí.

    De `secretos_empresa` descifra 'matias_email'/'matias_password'; de `config_empresa` lee
    'matias_base_url'/'matias_resolution'/'matias_prefix'/'matias_notes'/'matias_city_id'.
    """
    email = await _secreto(session, master, empresa_id, "matias_email")
    password = await _secreto(session, master, empresa_id, "matias_password")
    config = await _config(session, empresa_id)
    cred = MatiasCredenciales(
        email=email or "", password=password or "", base_url=config["matias_base_url"]
    )
    fiscal = ConfigFiscal(
        resolution_number=config["matias_resolution"], prefix=config["matias_prefix"],
        notes=config.get("matias_notes", ""), city_id_default=config.get("matias_city_id"),
    )
    return cred, fiscal
