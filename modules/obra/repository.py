"""Repositorio de obras y reportes diarios: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules.clientes.repository`. El soft delete (`eliminado_en`) oculta la obra: `obtener`/`listar`
filtran las borradas (para el API son 404 / no aparecen). El conteo de operación (`contar_operacion`)
son tres COUNT baratos apoyados en los índices `obra_id` de las tablas asociadas — sin agregados pesados
(el presupuesto vs. gasto real es Fase 3). La sesión del tenant ES la transacción; aquí no se hace commit.
"""
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.maquinaria.models import AsignacionMaquinaObra
from modules.obra.models import Obra, ReporteDiarioObra
from modules.obra.schemas import ObraCrear, ReporteDiarioCrear
from modules.trabajadores.models import AsignacionTrabajadorObra


@dataclass(frozen=True, slots=True)
class ConteosOperacion:
    maquinas_asignadas: int
    trabajadores_asignados: int
    reportes_diarios: int


class SqlObrasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def obtener(self, obra_id: int) -> Obra | None:
        """Obra vigente por id (las soft-deleted se tratan como inexistentes)."""
        return (
            await self._s.execute(
                select(Obra).where(Obra.id == obra_id, Obra.eliminado_en.is_(None))
            )
        ).scalar_one_or_none()

    async def listar(
        self, *, cliente_id: int | None = None, estado: str | None = None
    ) -> list[Obra]:
        """Obras vigentes (más recientes primero); filtra por cliente y por estado."""
        stmt = select(Obra).where(Obra.eliminado_en.is_(None))
        if cliente_id is not None:
            stmt = stmt.where(Obra.cliente_id == cliente_id)
        if estado is not None:
            stmt = stmt.where(Obra.estado == estado)
        stmt = stmt.order_by(Obra.creado_en.desc(), Obra.id.desc())
        return list((await self._s.execute(stmt)).scalars().all())

    async def crear(self, datos: ObraCrear) -> Obra:
        obra = Obra(**datos.model_dump())
        self._s.add(obra)
        await self._s.flush()  # asigna obra.id
        return obra

    async def actualizar(self, obra: Obra, cambios: dict) -> Obra:
        """Aplica un parche parcial sobre una obra ya cargada (solo las claves presentes)."""
        for campo, valor in cambios.items():
            setattr(obra, campo, valor)
        await self._s.flush()
        return obra

    async def cambiar_estado(self, obra: Obra, nuevo_estado: str) -> Obra:
        """Persiste el nuevo estado (la validación de la transición la hace el servicio)."""
        obra.estado = nuevo_estado
        await self._s.flush()
        return obra

    async def soft_delete(self, obra: Obra) -> None:
        """Marca la baja lógica (`eliminado_en = ahora` en hora Colombia); no borra la fila."""
        obra.eliminado_en = now_co()
        await self._s.flush()

    async def contar_operacion(self, obra_id: int) -> ConteosOperacion:
        """Tres COUNT baratos (máquinas/trabajadores/reportes) por sus índices `obra_id`."""
        maquinas = (
            await self._s.execute(
                select(func.count()).select_from(AsignacionMaquinaObra).where(
                    AsignacionMaquinaObra.obra_id == obra_id
                )
            )
        ).scalar_one()
        trabajadores = (
            await self._s.execute(
                select(func.count()).select_from(AsignacionTrabajadorObra).where(
                    AsignacionTrabajadorObra.obra_id == obra_id
                )
            )
        ).scalar_one()
        reportes = (
            await self._s.execute(
                select(func.count()).select_from(ReporteDiarioObra).where(
                    ReporteDiarioObra.obra_id == obra_id
                )
            )
        ).scalar_one()
        return ConteosOperacion(
            maquinas_asignadas=int(maquinas),
            trabajadores_asignados=int(trabajadores),
            reportes_diarios=int(reportes),
        )

    async def crear_reporte(
        self, obra_id: int, datos: ReporteDiarioCrear
    ) -> ReporteDiarioObra:
        """Inserta un reporte diario de avance ligado a la obra (la `fecha` ya viene resuelta)."""
        reporte = ReporteDiarioObra(obra_id=obra_id, **datos.model_dump())
        self._s.add(reporte)
        await self._s.flush()  # asigna reporte.id
        return reporte

    async def listar_reportes(
        self, obra_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[ReporteDiarioObra]:
        """Reportes diarios de una obra, más recientes primero.

        Paginado (el bot escribe un reporte por día; una obra de años acumula cientos de filas
        con texto + arrays de fotos): calca el kárdex de horas de máquina.
        """
        stmt = (
            select(ReporteDiarioObra)
            .where(ReporteDiarioObra.obra_id == obra_id)
            .order_by(ReporteDiarioObra.fecha.desc(), ReporteDiarioObra.id.desc())
            .limit(limite)
            .offset(offset)
        )
        return list((await self._s.execute(stmt)).scalars().all())
