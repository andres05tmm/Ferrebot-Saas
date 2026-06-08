"""Motor del pack FAQ / conocimiento (capa fina sobre el repositorio y el recuperador).

`responder` es lo que usa el agente: recupera las entradas relevantes (vía el puerto `Recuperador`,
intercambiable por embeddings/RAG luego) y las devuelve para que el modelo COMPONGA la respuesta — el
servicio no redacta. El resto son operaciones CRUD para el dashboard. No escribe SQL (regla #2).
"""
from __future__ import annotations

from dataclasses import dataclass

from modules.faq.errors import ConocimientoInexistente
from modules.faq.models import Conocimiento
from modules.faq.repository import ConocimientoRepo
from modules.faq.retrieval import Recuperador, RecuperadorKeyword
from modules.faq.schemas import ConocimientoCrear


@dataclass(frozen=True, slots=True)
class ResultadoFaq:
    """Entradas recuperadas para una pregunta. `hay_info=False` → el agente NO debe inventar."""

    entradas: list[Conocimiento]

    @property
    def hay_info(self) -> bool:
        return bool(self.entradas)


class FaqService:
    def __init__(self, repo: ConocimientoRepo, *, recuperador: Recuperador | None = None) -> None:
        self._repo = repo
        # Recuperador por defecto = keyword (v1); el composition root puede inyectar otro (RAG, v2).
        self._recuperador = recuperador or RecuperadorKeyword(repo)

    # --- de cara al cliente (lo usa el agente) -------------------------------
    async def responder(self, pregunta: str) -> ResultadoFaq:
        """Recupera el conocimiento relevante para `pregunta`. Vacío = no hay información suficiente."""
        entradas = await self._recuperador.recuperar(pregunta)
        return ResultadoFaq(entradas=entradas)

    # --- dashboard (CRUD del conocimiento) -----------------------------------
    async def listar(self, *, solo_activas: bool = True) -> list[Conocimiento]:
        return await self._repo.listar(solo_activas=solo_activas)

    async def obtener(self, conocimiento_id: int) -> Conocimiento:
        entrada = await self._repo.por_id(conocimiento_id)
        if entrada is None:
            raise ConocimientoInexistente(conocimiento_id)
        return entrada

    async def crear(self, datos: ConocimientoCrear) -> Conocimiento:
        return await self._repo.crear(datos)

    async def actualizar(self, conocimiento_id: int, datos: ConocimientoCrear) -> Conocimiento:
        entrada = await self.obtener(conocimiento_id)
        return await self._repo.actualizar(entrada, datos)

    async def eliminar(self, conocimiento_id: int) -> None:
        """Borrado DURO: el conocimiento no es histórico fiscal; quitar una entrada la elimina."""
        entrada = await self.obtener(conocimiento_id)
        await self._repo.eliminar(entrada)
