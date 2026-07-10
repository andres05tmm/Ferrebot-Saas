"""Repositorio de trabajadores: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules.clientes.repository`. El soft delete (`eliminado_en`) marca la ocultación del registro:
`obtener`/`listar` filtran los borrados (para el API son 404 / no aparecen), pero `buscar_por_documento`
NO los filtra, porque la columna `documento` es UNIQUE en la base y abarca también las filas borradas
(así el chequeo de duplicado del servicio coincide con la constraint y no se filtra un IntegrityError).
La sesión del tenant ES la transacción; aquí no se hace commit.
"""
from datetime import date

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from core.events import publish
from modules.trabajadores.models import AsignacionTrabajadorObra, Trabajador
from modules.trabajadores.schemas import TrabajadorCrear
# Lectura SOLO de existencia/estado de la obra (patrón Ola A: modelo congelado de otro paquete, sin
# relationship). No se escribe sobre `obras` desde este repo.
from modules.obra.models import Obra


class SqlTrabajadoresRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def buscar_por_documento(self, documento: str) -> Trabajador | None:
        """Trabajador con ese documento, INCLUIDOS los soft-deleted (la unicidad los abarca)."""
        return (
            await self._s.execute(select(Trabajador).where(Trabajador.documento == documento))
        ).scalar_one_or_none()

    async def obtener(self, trabajador_id: int) -> Trabajador | None:
        """Trabajador vigente por id (los soft-deleted se tratan como inexistentes)."""
        return (
            await self._s.execute(
                select(Trabajador).where(
                    Trabajador.id == trabajador_id, Trabajador.eliminado_en.is_(None)
                )
            )
        ).scalar_one_or_none()

    async def listar(
        self, *, tipo_vinculacion: str | None = None, activo: bool | None = None
    ) -> list[Trabajador]:
        """Trabajadores vigentes, ordenados por apellidos/nombres; filtra por vínculo y por `activo`."""
        stmt = select(Trabajador).where(Trabajador.eliminado_en.is_(None))
        if tipo_vinculacion is not None:
            stmt = stmt.where(Trabajador.tipo_vinculacion == tipo_vinculacion)
        if activo is not None:
            stmt = stmt.where(Trabajador.activo == activo)
        stmt = stmt.order_by(Trabajador.apellidos, Trabajador.nombres)
        return list((await self._s.execute(stmt)).scalars().all())

    async def crear(self, datos: TrabajadorCrear) -> Trabajador:
        trabajador = Trabajador(**datos.model_dump())
        self._s.add(trabajador)
        await self._s.flush()  # asigna trabajador.id
        return trabajador

    async def actualizar(self, trabajador: Trabajador, cambios: dict) -> Trabajador:
        """Aplica un parche parcial sobre una instancia ya cargada (solo las claves presentes)."""
        for campo, valor in cambios.items():
            setattr(trabajador, campo, valor)
        await self._s.flush()
        return trabajador

    async def soft_delete(self, trabajador: Trabajador) -> None:
        """Marca la baja lógica (`eliminado_en = ahora` en hora Colombia, regla #4); no borra la fila."""
        trabajador.eliminado_en = now_co()
        await self._s.flush()

    # ---- CRUD de asignaciones trabajador→obra (Calendario de obra) -------------------------------
    async def listar_asignaciones(self, trabajador_id: int) -> list[AsignacionTrabajadorObra]:
        """Asignaciones a obra de un trabajador, la más reciente primero."""
        stmt = (
            select(AsignacionTrabajadorObra)
            .where(AsignacionTrabajadorObra.trabajador_id == trabajador_id)
            .order_by(
                AsignacionTrabajadorObra.fecha_inicio.desc(), AsignacionTrabajadorObra.id.desc()
            )
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def obtener_asignacion(
        self, trabajador_id: int, asignacion_id: int
    ) -> AsignacionTrabajadorObra | None:
        """Asignación por id ACOTADA a su trabajador (una de otro trabajador es inexistente para éste)."""
        return (
            await self._s.execute(
                select(AsignacionTrabajadorObra).where(
                    AsignacionTrabajadorObra.id == asignacion_id,
                    AsignacionTrabajadorObra.trabajador_id == trabajador_id,
                )
            )
        ).scalar_one_or_none()

    async def asignacion_solapada(
        self,
        trabajador_id: int,
        fecha_inicio: date,
        fecha_fin: date | None,
        *,
        excluir_id: int | None = None,
    ) -> bool:
        """¿Hay otra asignación ACTIVA del trabajador cuyo rango se cruza con [fecha_inicio, fecha_fin]?

        Mismas reglas que la máquina: intervalos con `fecha_fin` NULL = abiertos; solape cuando
        `nueva.inicio <= existente.fin` Y `nueva.fin >= existente.inicio`. Solo filas `activa=true`."""
        stmt = select(AsignacionTrabajadorObra.id).where(
            AsignacionTrabajadorObra.trabajador_id == trabajador_id,
            AsignacionTrabajadorObra.activa.is_(True),
            or_(
                AsignacionTrabajadorObra.fecha_fin.is_(None),
                AsignacionTrabajadorObra.fecha_fin >= fecha_inicio,
            ),
        )
        if fecha_fin is not None:
            stmt = stmt.where(AsignacionTrabajadorObra.fecha_inicio <= fecha_fin)
        if excluir_id is not None:
            stmt = stmt.where(AsignacionTrabajadorObra.id != excluir_id)
        return (await self._s.execute(stmt.limit(1))).first() is not None

    async def obra_asignable(self, obra_id: int) -> str | None:
        """Estado de la obra viva por id, o None si no existe / está soft-deleted (para el mapeo 404/409)."""
        return (
            await self._s.execute(
                select(Obra.estado).where(Obra.id == obra_id, Obra.eliminado_en.is_(None))
            )
        ).scalar_one_or_none()

    async def crear_asignacion(
        self,
        *,
        trabajador_id: int,
        obra_id: int,
        fecha_inicio: date,
        fecha_fin: date | None,
        activa: bool = True,
    ) -> AsignacionTrabajadorObra:
        """Inserta la asignación, hace flush (asigna `id`) y emite el evento SSE del calendario."""
        asig = AsignacionTrabajadorObra(
            trabajador_id=trabajador_id,
            obra_id=obra_id,
            fecha_inicio=fecha_inicio,
            fecha_fin=fecha_fin,
            activa=activa,
        )
        self._s.add(asig)
        await self._s.flush()  # asigna asig.id
        await self._publicar_asignacion(asig)
        return asig

    async def actualizar_asignacion(
        self, asig: AsignacionTrabajadorObra, cambios: dict
    ) -> AsignacionTrabajadorObra:
        """Aplica un parche parcial (solo claves presentes) y reemite el evento. La tabla no tiene
        `actualizado_en`, así que no hace falta `refresh`."""
        for campo, valor in cambios.items():
            setattr(asig, campo, valor)
        await self._s.flush()
        await self._publicar_asignacion(asig)
        return asig

    async def _publicar_asignacion(self, asig: AsignacionTrabajadorObra) -> None:
        """Evento SSE que consume el calendario de obra (alta y edición comparten payload)."""
        await publish(
            self._s,
            "asignacion_trabajador_actualizada",
            {
                "asignacion_id": asig.id,
                "trabajador_id": asig.trabajador_id,
                "obra_id": asig.obra_id,
                "activa": asig.activa,
            },
        )

    # ---- Calendario de obra (commit 2): lecturas de OPERACIÓN por rango [desde, hasta] ---------------
    # Una consulta por origen para todo el rango (N+1-free); el service agrega en Python. Nombre del
    # trabajador con el patrón NULLIF(TRIM(...)) del repo. Filtros opcionales como WHERE condicional.
    async def asistencia_calendario(
        self,
        desde: date,
        hasta: date,
        *,
        trabajador_id: int | None = None,
        obra_id: int | None = None,
    ) -> list[dict]:
        """Asistencia en el rango + nombre de trabajador. LEFT JOIN obras: `obra_id` NULL (día
        administrativo) se conserva con `obra=null`."""
        params: dict = {"desde": desde, "hasta": hasta}
        extra = ""
        if trabajador_id is not None:
            extra += " AND a.trabajador_id = :trabajador_id"
            params["trabajador_id"] = trabajador_id
        if obra_id is not None:
            extra += " AND a.obra_id = :obra_id"
            params["obra_id"] = obra_id
        sql = (
            "SELECT a.id, a.trabajador_id, "
            "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') AS trabajador, "
            "  a.obra_id, o.nombre AS obra, a.horas_trabajadas, a.horas_extra_diurnas, "
            "  a.horas_extra_nocturnas, a.horas_dominical_festivo, a.ausencia, a.fecha "
            "FROM registros_asistencia a "
            "JOIN trabajadores t ON t.id = a.trabajador_id "
            "LEFT JOIN obras o ON o.id = a.obra_id "
            "WHERE a.fecha BETWEEN :desde AND :hasta" + extra + " ORDER BY a.fecha, a.id"
        )
        return [dict(f._mapping) for f in (await self._s.execute(text(sql), params)).all()]

    async def estado_trabajadores_hoy(self, hoy: date) -> list[dict]:
        """Estado ACTUAL: asignaciones trabajador→obra ACTIVAS vigentes hoy + nombres (obra y desde).

        Vigente = `activa` AND `fecha_inicio <= hoy <= fecha_fin`/NULL. `DISTINCT ON (trabajador_id)` con
        `ORDER BY trabajador_id, fecha_inicio DESC` colapsa varias vigentes (dato viejo) a la de arranque
        más reciente. La relación con la máquina la compone el service (operador de `estado_maquinas_hoy`);
        aquí solo la asignación trabajador→obra. Una sola consulta (sin N+1)."""
        sql = (
            "SELECT DISTINCT ON (ato.trabajador_id) ato.trabajador_id, "
            "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') AS trabajador, "
            "  ato.obra_id, o.nombre AS obra, ato.fecha_inicio AS desde "
            "FROM asignaciones_trabajador_obra ato "
            "JOIN trabajadores t ON t.id = ato.trabajador_id "
            "JOIN obras o ON o.id = ato.obra_id "
            "WHERE ato.activa AND ato.fecha_inicio <= :hoy "
            "  AND (ato.fecha_fin IS NULL OR ato.fecha_fin >= :hoy) "
            "ORDER BY ato.trabajador_id, ato.fecha_inicio DESC, ato.id DESC"
        )
        return [dict(f._mapping) for f in (await self._s.execute(text(sql), {"hoy": hoy})).all()]

    async def asignaciones_trabajador_calendario(
        self,
        desde: date,
        hasta: date,
        *,
        trabajador_id: int | None = None,
        obra_id: int | None = None,
    ) -> list[dict]:
        """Asignaciones trabajador→obra ACTIVAS cuyo rango SOLAPA [desde, hasta] + nombres.

        Solape = `fecha_inicio <= hasta AND (fecha_fin IS NULL OR fecha_fin >= desde)`. Devuelve rangos;
        el service los proyecta a cada día que cubren."""
        params: dict = {"desde": desde, "hasta": hasta}
        extra = ""
        if trabajador_id is not None:
            extra += " AND ato.trabajador_id = :trabajador_id"
            params["trabajador_id"] = trabajador_id
        if obra_id is not None:
            extra += " AND ato.obra_id = :obra_id"
            params["obra_id"] = obra_id
        sql = (
            "SELECT ato.id AS asignacion_id, ato.trabajador_id, "
            "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') AS trabajador, "
            "  ato.obra_id, o.nombre AS obra, ato.fecha_inicio, ato.fecha_fin "
            "FROM asignaciones_trabajador_obra ato "
            "JOIN trabajadores t ON t.id = ato.trabajador_id "
            "JOIN obras o ON o.id = ato.obra_id "
            "WHERE ato.activa AND ato.fecha_inicio <= :hasta "
            "  AND (ato.fecha_fin IS NULL OR ato.fecha_fin >= :desde)" + extra
            + " ORDER BY ato.fecha_inicio, ato.id"
        )
        return [dict(f._mapping) for f in (await self._s.execute(text(sql), params)).all()]
