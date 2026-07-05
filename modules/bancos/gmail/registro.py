"""Acceso al registro `gmail_cuentas` del CONTROL DB (token→empresa, estado del watch/history).

Estado operativo del buzón por empresa: el `webhook_token` (mapea el push global → tenant), el
`last_history_id` procesado y `watch_expira` (los barre el cron sin abrir cada tenant). Los secretos
OAuth NO viven aquí (van cifrados en `secretos_empresa`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class CuentaGmail:
    empresa_id: int
    proposito: str
    email: str | None
    pubsub_topic: str | None
    last_history_id: str | None
    watch_expira: datetime | None


class RegistroGmail:
    """CRUD mínimo sobre `gmail_cuentas` (control DB). Una instancia por sesión de control."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def resolver_por_token(self, token: str) -> CuentaGmail | None:
        """Empresa + estado del buzón por el token opaco del webhook. None = token no registrado."""
        row = (await self._s.execute(
            text("SELECT empresa_id, proposito, email, pubsub_topic, last_history_id, watch_expira "
                 "FROM gmail_cuentas WHERE webhook_token = :t AND activo"),
            {"t": token},
        )).first()
        return None if row is None else CuentaGmail(*row)

    async def por_empresa(self, empresa_id: int, proposito: str = "bancolombia") -> CuentaGmail | None:
        row = (await self._s.execute(
            text("SELECT empresa_id, proposito, email, pubsub_topic, last_history_id, watch_expira "
                 "FROM gmail_cuentas WHERE empresa_id = :e AND proposito = :p AND activo"),
            {"e": empresa_id, "p": proposito},
        )).first()
        return None if row is None else CuentaGmail(*row)

    async def cuentas_activas(self, proposito: str = "bancolombia") -> list[CuentaGmail]:
        """Todas las cuentas activas de un propósito (para el cron de renovación del watch)."""
        rows = (await self._s.execute(
            text("SELECT empresa_id, proposito, email, pubsub_topic, last_history_id, watch_expira "
                 "FROM gmail_cuentas WHERE proposito = :p AND activo ORDER BY empresa_id"),
            {"p": proposito},
        )).all()
        return [CuentaGmail(*r) for r in rows]

    async def guardar_history(self, empresa_id: int, history_id: str, proposito: str = "bancolombia") -> None:
        await self._s.execute(
            text("UPDATE gmail_cuentas SET last_history_id = :h WHERE empresa_id = :e AND proposito = :p"),
            {"h": history_id, "e": empresa_id, "p": proposito},
        )

    async def guardar_watch(
        self, empresa_id: int, *, history_id: str | None, expira: datetime | None,
        proposito: str = "bancolombia",
    ) -> None:
        await self._s.execute(
            text("UPDATE gmail_cuentas SET watch_expira = :x, "
                 "last_history_id = COALESCE(:h, last_history_id) "
                 "WHERE empresa_id = :e AND proposito = :p"),
            {"x": expira, "h": history_id, "e": empresa_id, "p": proposito},
        )
