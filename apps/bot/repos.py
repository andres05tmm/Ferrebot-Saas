"""Implementaciones reales de los puertos del bot (control DB + base del tenant).

SQL solo aquí (regla no negociable #2). Espejan el patrón de `core.llm.stores`:
- `ControlSecretosBot` descifra `secretos_empresa` (claves `telegram_webhook_secret`,
  `telegram_token`) — la pre-validación del webhook sí lee el control DB (plano de control).
- `ControlCapacidades` calcula las features efectivas (plan ± `empresa_features`).
- `SqlUsuariosBotRepo` mapea `telegram_id` → usuario sobre la sesión del tenant.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.bot.ports import UsuarioBot
from core.crypto import decrypt_split
from core.tenancy.capacidades import ControlCapacidades  # reexport (compat): se movió a core.tenancy

__all__ = ["ControlSecretosBot", "ControlCapacidades", "SqlUsuariosBotRepo"]

# Claves en secretos_empresa (schema.md §secretos_empresa).
_CLAVE_WEBHOOK_SECRET = "telegram_webhook_secret"
_CLAVE_BOT_TOKEN = "telegram_token"


class ControlSecretosBot:
    """Lee y descifra el secret-token del webhook y el token del bot desde el control DB."""

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
        return await self._leer(empresa_id, _CLAVE_WEBHOOK_SECRET)

    async def bot_token(self, empresa_id: int) -> str | None:
        return await self._leer(empresa_id, _CLAVE_BOT_TOKEN)


class SqlUsuariosBotRepo:
    """Mapea telegram_id → usuario activo sobre la base del tenant (usuarios.telegram_id)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def por_telegram_id(self, telegram_id: int) -> UsuarioBot | None:
        row = (
            await self._s.execute(
                text("SELECT id, rol, activo FROM usuarios WHERE telegram_id = :t"),
                {"t": telegram_id},
            )
        ).first()
        if row is None:
            return None
        return UsuarioBot(id=row[0], rol=row[1], activo=row[2])
