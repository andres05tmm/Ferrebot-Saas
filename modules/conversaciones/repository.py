"""Repositorio del pack de conversación / handoff: único lugar con SQL del módulo (regla #2).

`escalar` hace upsert por `cliente_telefono` (una conversación por cliente): la crea en `humano` o
re-escala la existente. `resolver` la devuelve a `bot` y sella `resuelta_en`. Cada transición emite su
evento SSE (`publish` → pg_notify, acotado al tenant, en la MISMA transacción) para que la bandeja del
dashboard se actualice en vivo. Fechas en hora Colombia (`now_co`, regla no negociable #4).
"""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.events import publish
from modules.conversaciones.models import Conversacion


class ConversacionRepo(Protocol):
    """Puerto de datos del pack (lo implementa `SqlConversacionRepository`; los tests lo falsean)."""

    async def por_telefono(self, telefono: str) -> Conversacion | None: ...
    async def por_id(self, conversacion_id: int) -> Conversacion | None: ...
    async def listar_por_estado(self, estado: str) -> list[Conversacion]: ...
    async def escalar(self, telefono: str, motivo: str | None) -> Conversacion: ...
    async def resolver(self, conversacion: Conversacion) -> Conversacion: ...


class SqlConversacionRepository:
    """Implementación SQL del puerto sobre la sesión del tenant (regla de multitenancy #2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def por_telefono(self, telefono: str) -> Conversacion | None:
        return (
            await self._s.execute(
                select(Conversacion).where(Conversacion.cliente_telefono == telefono)
            )
        ).scalar_one_or_none()

    async def por_id(self, conversacion_id: int) -> Conversacion | None:
        return await self._s.get(Conversacion, conversacion_id)

    async def listar_por_estado(self, estado: str) -> list[Conversacion]:
        """Conversaciones en un estado, las escaladas más recientes primero (para la bandeja)."""
        stmt = (
            select(Conversacion)
            .where(Conversacion.estado == estado)
            .order_by(Conversacion.escalada_en.desc().nullslast(), Conversacion.id.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def escalar(self, telefono: str, motivo: str | None) -> Conversacion:
        """Marca la conversación del cliente como `humano` (upsert por teléfono). Idempotente al estado.

        Si ya existe, re-escala (nuevo motivo/`escalada_en`, limpia `resuelta_en`); si no, la crea.
        """
        ahora = now_co()
        conv = await self.por_telefono(telefono)
        if conv is None:
            conv = Conversacion(
                cliente_telefono=telefono, estado="humano", motivo=motivo, escalada_en=ahora
            )
            self._s.add(conv)
        else:
            conv.estado = "humano"
            conv.motivo = motivo
            conv.escalada_en = ahora
            conv.resuelta_en = None
        await self._s.flush()
        await publish(self._s, "conversacion_escalada", {
            "conversacion_id": conv.id, "cliente_telefono": conv.cliente_telefono,
            "motivo": conv.motivo, "estado": conv.estado,
        })
        return conv

    async def resolver(self, conversacion: Conversacion) -> Conversacion:
        """Devuelve la conversación al bot (`estado=bot`) y sella `resuelta_en`."""
        conversacion.estado = "bot"
        conversacion.resuelta_en = now_co()
        await self._s.flush()
        await publish(self._s, "conversacion_resuelta", {
            "conversacion_id": conversacion.id,
            "cliente_telefono": conversacion.cliente_telefono, "estado": conversacion.estado,
        })
        return conversacion
