"""Motor del pack de conversación / handoff (capa fina sobre el repositorio).

`escalar` y `resolver` orquestan la transición de estado; `esta_en_humano` es el predicado que el
runtime de WhatsApp consulta ANTES de correr el agente (si la conversación está en `humano`, no lo
corre). No escribe SQL (regla #2): delega en `ConversacionRepo`.
"""
from modules.conversaciones.errors import ConversacionInexistente
from modules.conversaciones.models import Conversacion
from modules.conversaciones.repository import ConversacionRepo


class ConversacionService:
    def __init__(self, repo: ConversacionRepo) -> None:
        self._repo = repo

    async def esta_en_humano(self, telefono: str) -> bool:
        """True si la conversación del cliente está escalada a un humano (el runtime debe pausar)."""
        conv = await self._repo.por_telefono(telefono)
        return conv is not None and conv.estado == "humano"

    async def escalar(self, telefono: str, *, motivo: str | None = None) -> Conversacion:
        """Marca la conversación del cliente como atendida por un humano."""
        return await self._repo.escalar(telefono, motivo)

    async def resolver(self, conversacion_id: int) -> Conversacion:
        """Devuelve la conversación al bot. Lanza `ConversacionInexistente` si no existe."""
        conv = await self._repo.por_id(conversacion_id)
        if conv is None:
            raise ConversacionInexistente(conversacion_id)
        return await self._repo.resolver(conv)

    async def listar_escaladas(self) -> list[Conversacion]:
        """Conversaciones en estado `humano` (la bandeja de handoff del dashboard)."""
        return await self._repo.listar_por_estado("humano")
