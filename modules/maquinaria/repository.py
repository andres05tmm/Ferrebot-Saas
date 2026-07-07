"""Repositorio de maquinaria: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules/inventario/repository.py`: la sesión del tenant ES la transacción; el aislamiento lo da
la base (sin `empresa_id`). Soft delete por `eliminado_en` (NULL = viva): las lecturas ocultan las
eliminadas y `codigo_existe` mira TODAS las filas (incluidas las borradas) porque el UNIQUE de la BD no
distingue soft delete —así el 409 se anticipa en vez de reventar como IntegrityError al hacer flush.
"""
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.maquinaria.models import (
    AsignacionMaquinaObra,
    Maquina,
    RegistroHorasMaquina,
)
from modules.maquinaria.schemas import MaquinaCrear


class SqlMaquinasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def listar(self, *, estado: str | None = None, q: str | None = None) -> list[Maquina]:
        """Máquinas vivas (no eliminadas) ordenadas por código; filtra por `estado` y/o `q`
        (código o nombre, ILIKE)."""
        stmt = select(Maquina).where(Maquina.eliminado_en.is_(None))
        if estado is not None:
            stmt = stmt.where(Maquina.estado == estado)
        if q:
            patron = f"%{q}%"
            stmt = stmt.where(or_(Maquina.codigo.ilike(patron), Maquina.nombre.ilike(patron)))
        stmt = stmt.order_by(Maquina.codigo)
        return list((await self._s.execute(stmt)).scalars().all())

    async def obtener(self, maquina_id: int) -> Maquina | None:
        """Máquina viva por id (una eliminada se trata como inexistente → 404)."""
        return (
            await self._s.execute(
                select(Maquina).where(
                    Maquina.id == maquina_id, Maquina.eliminado_en.is_(None)
                )
            )
        ).scalar_one_or_none()

    async def codigo_existe(self, codigo: str, *, excluir_id: int | None = None) -> bool:
        """¿Otra máquina ya usa este código? Mira TODAS las filas (el UNIQUE de la BD incluye las
        soft-deleted); `excluir_id` se ignora a sí mismo al editar."""
        stmt = select(Maquina.id).where(Maquina.codigo == codigo)
        if excluir_id is not None:
            stmt = stmt.where(Maquina.id != excluir_id)
        return (await self._s.execute(stmt.limit(1))).first() is not None

    async def crear(self, datos: MaquinaCrear) -> Maquina:
        maquina = Maquina(**datos.model_dump())
        self._s.add(maquina)
        await self._s.flush()  # asigna maquina.id
        return maquina

    async def actualizar(self, maquina: Maquina, cambios: dict) -> Maquina:
        """Aplica `cambios` (dict campo→valor ya validado) sobre la máquina cargada. `actualizado_en`
        lo refresca el `onupdate` del ORM en el flush."""
        for campo, valor in cambios.items():
            setattr(maquina, campo, valor)
        await self._s.flush()
        return maquina

    async def soft_delete(self, maquina_id: int) -> bool:
        """Marca la máquina como eliminada (`eliminado_en = ahora Colombia`); nunca hard-delete
        (la referencian asignaciones/horas/mantenimientos). Devuelve False si no existe o ya estaba
        eliminada."""
        maquina = await self.obtener(maquina_id)
        if maquina is None:
            return False
        maquina.eliminado_en = now_co()
        await self._s.flush()
        return True

    # ---- Lecturas de operación (solo lectura; el registro es de Fase 3) ------
    async def listar_asignaciones(self, maquina_id: int) -> list[AsignacionMaquinaObra]:
        """Asignaciones a obra de una máquina, la más reciente primero."""
        stmt = (
            select(AsignacionMaquinaObra)
            .where(AsignacionMaquinaObra.maquina_id == maquina_id)
            .order_by(AsignacionMaquinaObra.fecha_inicio.desc(), AsignacionMaquinaObra.id.desc())
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def listar_horas(
        self, maquina_id: int, *, limite: int = 100, offset: int = 0
    ) -> list[RegistroHorasMaquina]:
        """Partes de horas de una máquina (kárdex de operación), el más reciente primero."""
        stmt = (
            select(RegistroHorasMaquina)
            .where(RegistroHorasMaquina.maquina_id == maquina_id)
            .order_by(RegistroHorasMaquina.fecha.desc(), RegistroHorasMaquina.id.desc())
            .limit(limite)
            .offset(offset)
        )
        return list((await self._s.execute(stmt)).scalars().all())
