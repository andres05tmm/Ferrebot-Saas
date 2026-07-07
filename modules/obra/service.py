"""Servicio de obras: validación de dominio sobre el repositorio (sin SQL).

El corazón del módulo es el CICLO DE VIDA de la obra: las transiciones de estado son EXPLÍCITAS y se
validan contra `_TRANSICIONES` (nada de estados imposibles). Una transición no contemplada →
`TransicionEstadoInvalida` (409). Operar sobre una obra inexistente o dada de baja → `ObraInexistente`
(404). Los reportes diarios exigen que la obra exista.

Ciclo de vida (v1): PLANIFICADA arranca la obra; entra en ejecución o se suspende antes de empezar; una
obra en ejecución se suspende o finaliza; una suspendida se reanuda o finaliza; una FINALIZADA se liquida
(cierre). LIQUIDADA es terminal (no admite más transiciones). El servicio depende del puerto `ObrasRepo`
(lo implementa `SqlObrasRepository`; los tests lo falsean).
"""
from typing import Protocol

from core.config.timezone import today_co
from modules.obra.errors import ObraInexistente, TransicionEstadoInvalida
from modules.obra.models import CotizacionObra, Obra, ReporteDiarioObra
from modules.obra.repository import ConteosOperacion
from modules.obra.schemas import ObraActualizar, ObraCrear, ReporteDiarioCrear

# Transiciones permitidas del ciclo de vida de una obra (destinos válidos por estado actual).
_TRANSICIONES: dict[str, frozenset[str]] = {
    "PLANIFICADA": frozenset({"EN_EJECUCION", "SUSPENDIDA"}),
    "EN_EJECUCION": frozenset({"SUSPENDIDA", "FINALIZADA"}),
    "SUSPENDIDA": frozenset({"EN_EJECUCION", "FINALIZADA"}),
    "FINALIZADA": frozenset({"LIQUIDADA"}),
    "LIQUIDADA": frozenset(),  # terminal
}


class ObrasRepo(Protocol):
    """Puerto de datos de obras (lo implementa SqlObrasRepository; los tests lo falsean)."""

    async def obtener(self, obra_id: int) -> Obra | None: ...
    async def listar(
        self, *, cliente_id: int | None = None, estado: str | None = None
    ) -> list[Obra]: ...
    async def crear(self, datos: ObraCrear) -> Obra: ...
    async def obtener_por_cotizacion(self, cotizacion_id: int) -> Obra | None: ...
    async def crear_desde_cotizacion(self, cotizacion: CotizacionObra) -> Obra: ...
    async def actualizar(self, obra: Obra, cambios: dict) -> Obra: ...
    async def cambiar_estado(self, obra: Obra, nuevo_estado: str) -> Obra: ...
    async def soft_delete(self, obra: Obra) -> None: ...
    async def contar_operacion(self, obra_id: int) -> ConteosOperacion: ...
    async def crear_reporte(
        self, obra_id: int, datos: ReporteDiarioCrear
    ) -> ReporteDiarioObra: ...
    async def listar_reportes(
        self, obra_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[ReporteDiarioObra]: ...


class ObrasService:
    def __init__(self, repo: ObrasRepo) -> None:
        self._repo = repo

    async def crear(self, datos: ObraCrear) -> Obra:
        """Da de alta una obra suelta (arranca PLANIFICADA por el default de la base)."""
        return await self._repo.crear(datos)

    async def crear_desde_cotizacion(self, cotizacion: CotizacionObra) -> Obra:
        """Crea la Obra 1-1 que nace de una cotización GANADA (método ADITIVO, lo llama la Fase 2).

        IDEMPOTENTE: `obras.cotizacion_id` es UNIQUE, así que una cotización ya convertida NO genera una
        segunda obra. Se resuelve mirando primero si ya existe una obra para esa cotización y, de ser
        así, devolviéndola; la UNIQUE de la base es el respaldo último ante una carrera. La `Obra`
        arranca PLANIFICADA (default de la base) y hereda cliente/nombre/ubicación de la cotización.
        """
        existente = await self._repo.obtener_por_cotizacion(cotizacion.id)
        if existente is not None:
            return existente
        return await self._repo.crear_desde_cotizacion(cotizacion)

    async def obtener(self, obra_id: int) -> Obra:
        obra = await self._repo.obtener(obra_id)
        if obra is None:
            raise ObraInexistente(obra_id)
        return obra

    async def resumen(self, obra_id: int) -> tuple[Obra, ConteosOperacion]:
        """Obra + conteos de operación (para el detalle). 404 si no existe."""
        obra = await self.obtener(obra_id)
        conteos = await self._repo.contar_operacion(obra_id)
        return obra, conteos

    async def listar(
        self, *, cliente_id: int | None = None, estado: str | None = None
    ) -> list[Obra]:
        return await self._repo.listar(cliente_id=cliente_id, estado=estado)

    async def actualizar(self, obra_id: int, datos: ObraActualizar) -> Obra:
        """Parche parcial de metadatos. 404 si no existe. No toca `estado`."""
        obra = await self.obtener(obra_id)
        cambios = datos.model_dump(exclude_unset=True)
        return await self._repo.actualizar(obra, cambios)

    async def cambiar_estado(self, obra_id: int, nuevo_estado: str) -> Obra:
        """Aplica una transición de estado VÁLIDA. 404 si no existe; 409 si la transición no se permite."""
        obra = await self.obtener(obra_id)
        if nuevo_estado not in _TRANSICIONES.get(obra.estado, frozenset()):
            raise TransicionEstadoInvalida(obra.estado, nuevo_estado)
        return await self._repo.cambiar_estado(obra, nuevo_estado)

    async def eliminar(self, obra_id: int) -> None:
        """Baja lógica (soft delete). 404 si no existe o ya estaba dada de baja."""
        obra = await self.obtener(obra_id)
        await self._repo.soft_delete(obra)

    async def crear_reporte(
        self, obra_id: int, datos: ReporteDiarioCrear
    ) -> ReporteDiarioObra:
        """Registra un reporte diario de avance. 404 si la obra no existe.

        La `fecha` por defecto es hoy en hora Colombia (regla #4); se resuelve aquí antes de persistir.
        """
        await self.obtener(obra_id)  # valida existencia (404 si no)
        datos = datos.model_copy(update={"fecha": datos.fecha or today_co()})
        return await self._repo.crear_reporte(obra_id, datos)

    async def listar_reportes(
        self, obra_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[ReporteDiarioObra]:
        """Reportes diarios de una obra (más recientes primero). 404 si la obra no existe."""
        await self.obtener(obra_id)  # valida existencia (404 si no)
        return await self._repo.listar_reportes(obra_id, limite=limite, offset=offset)
