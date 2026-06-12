"""Motor del pack de conversación / handoff (capa fina sobre el repositorio).

`escalar` y `resolver` orquestan la transición de estado; `esta_en_humano` es el predicado que el
runtime de WhatsApp consulta ANTES de correr el agente (si la conversación está en `humano`, no lo
corre). No escribe SQL (regla #2): delega en `ConversacionRepo`.

Al RESOLVER (devolver al bot) se LIMPIA la memoria de conversación del cliente (puerto opcional
`MemoriaConversacion`): si no, el LLM re-escalaría de inmediato por el historial viejo (el cliente
había pedido asesor). Es best-effort: un fallo de la memoria no impide resolver.
"""
from typing import Protocol

from core.logging import get_logger
from modules.conversaciones.errors import ConversacionInexistente, ConversacionNoEnHumano
from modules.conversaciones.models import Conversacion, ConversacionMensaje
from modules.conversaciones.repository import ConversacionRepo, FilaInbox

log = get_logger("conversaciones.service")


class MemoriaConversacion(Protocol):
    """Puerto de la memoria de conversación del canal (lo satisface `apps.wa.agent.MemoriaWa`)."""

    async def limpiar(self, tenant_id: int, telefono: str) -> None: ...


class EnviadorWa(Protocol):
    """Puerto de envío saliente por WhatsApp (lo satisface el adaptador Kapso del router).

    Resuelve el número (`phone_number_id`) del tenant y manda el texto al cliente. Solo válido dentro
    de la ventana de 24h (texto libre); fuera de ella el frente deshabilita el composer.
    """

    async def enviar(self, tenant_id: int, to: str, texto: str) -> None: ...


class ConversacionService:
    def __init__(
        self,
        repo: ConversacionRepo,
        *,
        memoria: MemoriaConversacion | None = None,
        enviador: EnviadorWa | None = None,
    ) -> None:
        self._repo = repo
        # Memoria del canal (Redis) para limpiar al resolver. None = sin limpieza (p. ej. el worker,
        # que solo escala/consulta; resolver lo hace el dashboard, que sí inyecta la memoria).
        self._memoria = memoria
        # Envío saliente (Kapso) para que el asesor responda desde el inbox. None = sin envío (p. ej.
        # el runtime, que no responde como asesor; lo inyecta el router del dashboard).
        self._enviador = enviador

    async def esta_en_humano(self, telefono: str) -> bool:
        """True si la conversación del cliente está escalada a un humano (el runtime debe pausar)."""
        conv = await self._repo.por_telefono(telefono)
        return conv is not None and conv.estado == "humano"

    async def escalar(self, telefono: str, *, motivo: str | None = None) -> Conversacion:
        """Marca la conversación del cliente como atendida por un humano."""
        return await self._repo.escalar(telefono, motivo)

    async def resolver(self, conversacion_id: int, *, tenant_id: int | None = None) -> Conversacion:
        """Devuelve la conversación al bot y LIMPIA su memoria. Lanza `ConversacionInexistente` si no existe.

        La limpieza de memoria (para que el bot retome en limpio y no re-escale por el historial viejo)
        es best-effort: requiere `tenant_id` y un puerto de memoria; un fallo se registra y no impide
        resolver.
        """
        conv = await self._repo.por_id(conversacion_id)
        if conv is None:
            raise ConversacionInexistente(conversacion_id)
        resuelta = await self._repo.resolver(conv)
        if self._memoria is not None and tenant_id is not None:
            try:
                await self._memoria.limpiar(tenant_id, resuelta.cliente_telefono)
            except Exception:  # noqa: BLE001 — un fallo de la memoria no debe impedir resolver
                log.exception("conversacion_limpiar_memoria_error", conversacion_id=conversacion_id)
        return resuelta

    async def listar_escaladas(self) -> list[Conversacion]:
        """Conversaciones en estado `humano` (la bandeja de handoff del dashboard)."""
        return await self._repo.listar_por_estado("humano")

    async def listar_inbox(self) -> list[FilaInbox]:
        """Todas las conversaciones con su último mensaje y estado (la lista del inbox)."""
        return await self._repo.listar_inbox()

    async def listar_mensajes(self, conversacion_id: int) -> list[ConversacionMensaje]:
        """Hilo de una conversación. Lanza `ConversacionInexistente` si no existe."""
        conv = await self._repo.por_id(conversacion_id)
        if conv is None:
            raise ConversacionInexistente(conversacion_id)
        return await self._repo.listar_mensajes(conv.cliente_telefono)

    async def tomar(self, conversacion_id: int) -> Conversacion:
        """Takeover manual: pone la conversación en `humano` (pausa el bot). 404 si no existe."""
        conv = await self._repo.por_id(conversacion_id)
        if conv is None:
            raise ConversacionInexistente(conversacion_id)
        return await self._repo.tomar(conv)

    async def responder(
        self, conversacion_id: int, texto: str, *, tenant_id: int
    ) -> ConversacionMensaje:
        """Manda el texto del asesor al cliente (Kapso), lo persiste (`autor=asesor`) y emite SSE.

        Exige `estado=humano` (toma la conversación antes de responder) — si no, `ConversacionNoEnHumano`.
        Envía PRIMERO y persiste DESPUÉS: si el envío falla, no queda un mensaje fantasma sin entregar.
        """
        if self._enviador is None:
            raise RuntimeError("ConversacionService sin enviador: no puede responder")
        conv = await self._repo.por_id(conversacion_id)
        if conv is None:
            raise ConversacionInexistente(conversacion_id)
        if conv.estado != "humano":
            raise ConversacionNoEnHumano(conversacion_id)
        await self._enviador.enviar(tenant_id, conv.cliente_telefono, texto)
        return await self._repo.agregar_mensaje(conv.cliente_telefono, "saliente", "asesor", texto)
