"""Refresh_token OAuth de Gmail POR empresa, cifrado en `secretos_empresa` (control DB).

El refresh_token es un secreto (da acceso al buzón): va cifrado con `SECRETS_MASTER_KEY`, igual que el
token del bot y el secret del webhook (patrón `ControlSecretosBot`). La rotación (Google emite uno
nuevo) re-cifra en su lugar. `client_id/secret` NO están aquí: son de plataforma (settings).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt_split, encrypt_split

CLAVE_REFRESH = "gmail_refresh_token_bancolombia"


async def leer_refresh_token(session: AsyncSession, master: str, empresa_id: int) -> str | None:
    row = (await session.execute(
        text("SELECT valor_cifrado, nonce FROM secretos_empresa WHERE empresa_id=:e AND clave=:c"),
        {"e": empresa_id, "c": CLAVE_REFRESH},
    )).first()
    if row is None:
        return None
    return decrypt_split(bytes(row[0]), bytes(row[1]), master)


async def guardar_refresh_token(session: AsyncSession, master: str, empresa_id: int, token: str) -> None:
    """Upsert cifrado del refresh_token (crea o rota). No commitea (lo hace el caller)."""
    cifrado, nonce = encrypt_split(token, master)
    await session.execute(
        text(
            "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) "
            "VALUES (:e, :c, :v, :n) "
            "ON CONFLICT (empresa_id, clave) DO UPDATE SET valor_cifrado=:v, nonce=:n"
        ),
        {"e": empresa_id, "c": CLAVE_REFRESH, "v": cifrado, "n": nonce},
    )
