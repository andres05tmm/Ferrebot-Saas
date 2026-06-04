"""Stores respaldados por el control DB: override de config por empresa y key cifrada.

Implementan los puertos `ConfigStore`/`KeyStore` del factory. Único lugar con SQL del módulo
LLM (regla no negociable #2). El control DB es el plano global; aquí se lee, no se escribe.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.crypto import decrypt_split

# Proveedor → clave en secretos_empresa (Claude usa la key de Anthropic).
_CLAVE_SECRETO = {"claude": "anthropic_api_key", "openai": "openai_api_key"}


class ControlLLMConfigStore:
    """Lee `config_empresa` (clave→valor, texto plano no secreto) para una empresa."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def overrides(self, empresa_id: int) -> dict[str, str]:
        rows = (
            await self._s.execute(
                text("SELECT clave, valor FROM config_empresa WHERE empresa_id = :e"),
                {"e": empresa_id},
            )
        ).all()
        return {r[0]: r[1] for r in rows}


class ControlLLMKeyStore:
    """Lee y descifra la API key del proveedor desde `secretos_empresa`."""

    def __init__(self, session: AsyncSession, master_key: str) -> None:
        self._s = session
        self._master = master_key

    async def api_key(self, empresa_id: int, provider: str) -> str | None:
        clave = _CLAVE_SECRETO.get(provider, f"{provider}_api_key")
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
