"""Lectura/descifrado de los secretos del canal Telegram público desde el control DB (SQL solo aquí).

Espeja `apps.bot.repos.ControlSecretosBot`, pero con las claves del canal PÚBLICO — independientes del
bot interno de operación (`telegram_token` / `telegram_webhook_secret`):

  - `tg_publico_bot_token`       → token de @SiriusBot de BotFather (envío saliente por la Bot API).
  - `tg_publico_webhook_secret`  → secret-token del `setWebhook` (validación del webhook entrante).

Ambos cifrados en `secretos_empresa` con `SECRETS_MASTER_KEY` (regla no negociable #5). El descifrado
del secret del webhook es plano de control (se lee ANTES de abrir la base del tenant).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt_split

# Claves en secretos_empresa (schema.md §secretos_empresa).
CLAVE_BOT_TOKEN = "tg_publico_bot_token"
CLAVE_WEBHOOK_SECRET = "tg_publico_webhook_secret"


class SecretosTgPublico:
    """Descifra el secret-token del webhook y el token del bot del canal público desde el control DB."""

    def __init__(self, session: AsyncSession, master_key: str) -> None:
        self._s = session
        self._master = master_key

    async def _leer(self, empresa_id: int, clave: str) -> str | None:
        row = (
            await self._s.execute(
                text(
                    "SELECT valor_cifrado, nonce FROM secretos_empresa "
                    "WHERE empresa_id = :e AND clave = :c"
                ),
                {"e": empresa_id, "c": clave},
            )
        ).first()
        if row is None:
            return None
        return decrypt_split(bytes(row[0]), bytes(row[1]), self._master)

    async def webhook_secret(self, empresa_id: int) -> str | None:
        return await self._leer(empresa_id, CLAVE_WEBHOOK_SECRET)

    async def bot_token(self, empresa_id: int) -> str | None:
        return await self._leer(empresa_id, CLAVE_BOT_TOKEN)
