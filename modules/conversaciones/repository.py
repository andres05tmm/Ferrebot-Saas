"""Repositorio del pack de conversación / handoff: único lugar con SQL del módulo (regla #2).

`escalar` hace upsert por `cliente_telefono` (una conversación por cliente): la crea en `humano` o
re-escala la existente. `resolver` la devuelve a `bot` y sella `resuelta_en`. `tomar` es el takeover
manual (estado→humano aunque el bot no haya escalado). `agregar_mensaje` persiste cada turno del hilo
(inbox, 0024) y `listar_inbox`/`listar_mensajes` lo proyectan. Cada transición y cada mensaje emiten su
evento SSE (`publish` → pg_notify, acotado al tenant, en la MISMA transacción) para que el inbox del
dashboard se actualice en vivo. Fechas en hora Colombia (`now_co`, regla no negociable #4).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.events import publish
from modules.conversaciones.models import Conversacion, ConversacionMensaje


@dataclass(frozen=True, slots=True)
class FilaInbox:
    """Una conversación con su último mensaje (para la lista izquierda del inbox)."""

    conversacion: Conversacion
    ultimo: ConversacionMensaje | None


class ConversacionRepo(Protocol):
    """Puerto de datos del pack (lo implementa `SqlConversacionRepository`; los tests lo falsean)."""

    async def por_telefono(self, telefono: str) -> Conversacion | None: ...
    async def por_id(self, conversacion_id: int) -> Conversacion | None: ...
    async def listar_por_estado(self, estado: str) -> list[Conversacion]: ...
    async def escalar(self, telefono: str, motivo: str | None) -> Conversacion: ...
    async def resolver(self, conversacion: Conversacion) -> Conversacion: ...
    async def asegurar(self, telefono: str) -> Conversacion: ...
    async def tomar(self, conversacion: Conversacion) -> Conversacion: ...
    async def agregar_mensaje(
        self, telefono: str, direccion: str, autor: str, texto: str
    ) -> ConversacionMensaje: ...
    async def listar_mensajes(self, telefono: str) -> list[ConversacionMensaje]: ...
    async def listar_inbox(self) -> list[FilaInbox]: ...


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

    async def asegurar(self, telefono: str) -> Conversacion:
        """Get-or-create de la conversación del cliente en `bot` (sin escalar). Sin evento.

        El inbox lista TODAS las conversaciones desde `conversaciones`; el runtime la asegura en el
        primer mensaje del cliente para que aparezca aunque el bot la resuelva sin pasar por un humano.
        """
        conv = await self.por_telefono(telefono)
        if conv is None:
            conv = Conversacion(cliente_telefono=telefono, estado="bot")
            self._s.add(conv)
            await self._s.flush()
        return conv

    async def tomar(self, conversacion: Conversacion) -> Conversacion:
        """Takeover manual: pasa la conversación a `humano` (pausa el bot) aunque no la haya escalado él.

        Reusa la semántica de pausa (`estado=humano` ⇒ el runtime no corre el agente) y emite el mismo
        evento `conversacion_escalada` para que el inbox y la home se actualicen en vivo.
        """
        conversacion.estado = "humano"
        conversacion.escalada_en = now_co()
        conversacion.resuelta_en = None
        if not conversacion.motivo:
            conversacion.motivo = "Tomada por un asesor"
        await self._s.flush()
        await publish(self._s, "conversacion_escalada", {
            "conversacion_id": conversacion.id, "cliente_telefono": conversacion.cliente_telefono,
            "motivo": conversacion.motivo, "estado": conversacion.estado,
        })
        return conversacion

    async def agregar_mensaje(
        self, telefono: str, direccion: str, autor: str, texto: str
    ) -> ConversacionMensaje:
        """Persiste un mensaje del hilo y emite `conversacion_mensaje` (para refrescar lista e hilo)."""
        msg = ConversacionMensaje(
            cliente_telefono=telefono, direccion=direccion, autor=autor, texto=texto,
            creada_en=now_co(),
        )
        self._s.add(msg)
        await self._s.flush()
        await publish(self._s, "conversacion_mensaje", {
            "cliente_telefono": telefono, "direccion": direccion, "autor": autor,
            "mensaje_id": msg.id,
        })
        return msg

    async def listar_mensajes(self, telefono: str) -> list[ConversacionMensaje]:
        """Hilo de un cliente en orden cronológico (id desempata mensajes del mismo instante)."""
        stmt = (
            select(ConversacionMensaje)
            .where(ConversacionMensaje.cliente_telefono == telefono)
            .order_by(ConversacionMensaje.creada_en.asc(), ConversacionMensaje.id.asc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def listar_inbox(self) -> list[FilaInbox]:
        """Todas las conversaciones con su último mensaje, ordenadas por actividad reciente.

        Un único `DISTINCT ON (cliente_telefono)` trae el último mensaje de cada cliente (sin N+1); el
        orden final (por actividad) se arma en Python sobre listas acotadas (sin paginación, regla de
        performance: el volumen del inbox es chico).
        """
        convs = list((await self._s.execute(select(Conversacion))).scalars().all())
        ultimos_stmt = (
            select(ConversacionMensaje)
            .order_by(
                ConversacionMensaje.cliente_telefono,
                ConversacionMensaje.creada_en.desc(),
                ConversacionMensaje.id.desc(),
            )
            .distinct(ConversacionMensaje.cliente_telefono)
        )
        por_tel = {m.cliente_telefono: m for m in (await self._s.execute(ultimos_stmt)).scalars().all()}
        filas = [FilaInbox(conversacion=c, ultimo=por_tel.get(c.cliente_telefono)) for c in convs]
        filas.sort(key=_clave_actividad, reverse=True)
        return filas


def _clave_actividad(fila: FilaInbox):
    """Instante de la última actividad de la conversación (último mensaje, o el alta si no hay hilo)."""
    if fila.ultimo is not None:
        return fila.ultimo.creada_en
    return fila.conversacion.escalada_en or fila.conversacion.creada_en
