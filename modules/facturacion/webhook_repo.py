"""Registro del webhook MATIAS en el control DB (D7.1): token → empresa + secret cifrado.

SQL sobre la sesión de control que recibe (per-call), espejo de `modules/facturacion/config.py`. El
secret de la firma vive CIFRADO en `secretos_empresa` (clave `matias_webhook_secret`); el token y la
URL de callback (no secretos) en `webhooks_matias`. Resolver SIEMPRE por el token de la ruta, jamás
por el payload (tenancy.md §1)."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt_split, encrypt_split

# Clave del secret de la firma del webhook en `secretos_empresa` (cifrado, como el resto de secretos).
CLAVE_SECRET_WEBHOOK = "matias_webhook_secret"


async def buscar_empresa_por_token(session: AsyncSession, token: str) -> int | None:
    """empresa_id del registro de webhook con ese token, o None si no está registrado."""
    return (
        await session.execute(
            text("SELECT empresa_id FROM webhooks_matias WHERE token = :t"), {"t": token}
        )
    ).scalar_one_or_none()


async def leer_secret_webhook(session: AsyncSession, master: str, empresa_id: int) -> str | None:
    """Descifra el secret de la firma del webhook de la empresa (patrón `config._secreto`), o None."""
    row = (
        await session.execute(
            text("SELECT valor_cifrado, nonce FROM secretos_empresa WHERE empresa_id = :e AND clave = :c"),
            {"e": empresa_id, "c": CLAVE_SECRET_WEBHOOK},
        )
    ).first()
    return decrypt_split(bytes(row[0]), bytes(row[1]), master) if row is not None else None


async def guardar_registro_webhook(
    session: AsyncSession, master: str, empresa_id: int, *, token: str, callback_url: str, secret: str
) -> None:
    """UPSERT del registro (token + callback) y del secret CIFRADO. Idempotente por empresa (re-registro).

    Lo usa `tools.registrar_webhook_matias` tras dar de alta el webhook en MATIAS. Una empresa tiene un
    solo webhook (UNIQUE empresa_id): re-registrar reemplaza token/URL/secret."""
    await session.execute(
        text(
            "INSERT INTO webhooks_matias (empresa_id, token, callback_url) VALUES (:e, :t, :u) "
            "ON CONFLICT (empresa_id) DO UPDATE SET token = EXCLUDED.token, callback_url = EXCLUDED.callback_url"
        ),
        {"e": empresa_id, "t": token, "u": callback_url},
    )
    cifrado, nonce = encrypt_split(secret, master)
    await session.execute(
        text(
            "INSERT INTO secretos_empresa (empresa_id, clave, valor_cifrado, nonce) "
            "VALUES (:e, :c, :v, :n) "
            "ON CONFLICT (empresa_id, clave) DO UPDATE SET valor_cifrado = EXCLUDED.valor_cifrado, "
            "nonce = EXCLUDED.nonce, actualizado_en = now()"
        ),
        {"e": empresa_id, "c": CLAVE_SECRET_WEBHOOK, "v": cifrado, "n": nonce},
    )
