"""Config de pagos de una empresa: la llave Bold (cifrada) + base URL, del control DB.

Espejo de `modules/facturacion/config.py` (patrón `cargar_config_matias`): la consumen el worker
(conciliación) y el wiring del agente. `None` = el tenant no tiene PSP → modo manual.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt_split
from core.pagos.bold import BASE_URL_DEFAULT, BoldCredenciales


async def cargar_config_bold(
    session: AsyncSession, master: str, empresa_id: int
) -> BoldCredenciales | None:
    """Descifra la llave Bold del tenant (`secretos_empresa.bold_api_key`). None = sin PSP (manual).

    `config_empresa.bold_base_url` permite apuntar a un ambiente de pruebas del PSP.
    """
    fila = (
        await session.execute(
            text(
                "SELECT valor_cifrado, nonce FROM secretos_empresa "
                "WHERE empresa_id = :e AND clave = 'bold_api_key'"
            ),
            {"e": empresa_id},
        )
    ).first()
    if fila is None:
        return None
    api_key = decrypt_split(bytes(fila[0]), bytes(fila[1]), master)
    base = (
        await session.execute(
            text(
                "SELECT valor FROM config_empresa "
                "WHERE empresa_id = :e AND clave = 'bold_base_url'"
            ),
            {"e": empresa_id},
        )
    ).scalar_one_or_none()
    return BoldCredenciales(api_key=api_key, base_url=base or BASE_URL_DEFAULT)
