"""Servicio de memoria conversacional: historial + entidades recordadas (lógica pura, sin BD).

Todo es **best-effort**: el contexto RAG mejora el turno pero nunca debe romperlo. Si el repo
falla al leer, se degrada a vacío; si falla al escribir, se traga el error (lo loguea el llamador).
SQL solo en el repositorio (regla #2); el servicio depende del puerto `MemoriaRepo`, falseado en
los tests unitarios.

Decisiones E4 fijadas (no re-litigar):
  - Historial: últimos N=8 ASC por (chat_id, creado_en); se ignoran roles fuera de {user,assistant}.
  - Persistencia: trunca contenido > 20_000 chars.
  - Entidades: alcance por `clave = str(chat_id)`; tipos {ultimo_cliente, ultimo_producto};
    `valor` JSONB = {"id": ..., "nombre": ...}; upsert idempotente (ON CONFLICT en el repo).
"""
from typing import Protocol

from core.llm.base import Message
from core.logging import get_logger
from modules.memoria.schemas import EntidadGuardada, MensajeGuardado

log = get_logger("modules.memoria")

HISTORIAL_LIMITE = 8
MAX_CONTENIDO = 20_000
ROLES_VALIDOS = frozenset({"user", "assistant"})

TIPO_ULTIMO_CLIENTE = "ultimo_cliente"
TIPO_ULTIMO_PRODUCTO = "ultimo_producto"


class MemoriaRepo(Protocol):
    """Puerto de datos de la memoria (lo implementa SqlMemoriaRepository; los tests lo falsean)."""

    async def ultimos_mensajes(self, chat_id: int, limite: int) -> list[MensajeGuardado]: ...
    async def guardar_mensaje(self, chat_id: int, rol: str, contenido: str) -> None: ...
    async def upsert_entidad(self, tipo: str, clave: str, valor: dict) -> None: ...
    async def entidades_por_clave(self, clave: str) -> list[EntidadGuardada]: ...


class AudioLogsRepo(Protocol):
    """Puerto de la bitácora de voz (lo implementa SqlAudioLogsRepository; faked en tests)."""

    async def registrar(self, chat_id: int, transcripcion: str, duracion: int | None) -> None: ...


class MemoriaService:
    def __init__(self, repo: MemoriaRepo) -> None:
        self._repo = repo

    async def cargar_historial(
        self, chat_id: int, *, limite: int = HISTORIAL_LIMITE
    ) -> list[Message]:
        """Últimos `limite` mensajes ASC como `Message`, ignorando roles no {user,assistant}.

        Best-effort: [] si no hay nada o si el repo falla (el historial es opcional al turno).
        """
        try:
            mensajes = await self._repo.ultimos_mensajes(chat_id, limite)
        except Exception:
            log.warning("memoria_cargar_historial_fallo", chat_id=chat_id, exc_info=True)
            return []
        return [
            Message(role=m.rol, content=m.contenido)
            for m in mensajes
            if m.rol in ROLES_VALIDOS
        ]

    async def guardar_turno(self, chat_id: int, *, usuario: str, asistente: str) -> None:
        """Persiste el mensaje del usuario y el del asistente (trunca a MAX_CONTENIDO).

        Best-effort: si el repo lanza, no propaga (no romper la respuesta ya enviada).
        """
        try:
            await self._repo.guardar_mensaje(chat_id, "user", usuario[:MAX_CONTENIDO])
            await self._repo.guardar_mensaje(chat_id, "assistant", asistente[:MAX_CONTENIDO])
        except Exception:
            log.warning("memoria_guardar_turno_fallo", chat_id=chat_id, exc_info=True)

    async def recordar_entidad(self, chat_id: int, tipo: str, valor: dict) -> None:
        """Upsert de la entidad por (tipo, clave=str(chat_id)). Best-effort."""
        try:
            await self._repo.upsert_entidad(tipo, str(chat_id), valor)
        except Exception:
            log.warning("memoria_recordar_entidad_fallo", chat_id=chat_id, tipo=tipo, exc_info=True)

    async def leer_entidades(self, chat_id: int) -> dict[str, dict]:
        """Mapa tipo→valor de las entidades del chat. Best-effort: {} si falla o no hay."""
        try:
            entidades = await self._repo.entidades_por_clave(str(chat_id))
        except Exception:
            log.warning("memoria_leer_entidades_fallo", chat_id=chat_id, exc_info=True)
            return {}
        return {e.tipo: e.valor for e in entidades}
