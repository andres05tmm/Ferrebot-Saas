"""Repositorio de herramientas: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules/maquinaria/repository.py`: la sesión del tenant ES la transacción; el aislamiento lo da
la base. Soft delete por `eliminado_en` (NULL = viva). `codigo_existe` mira TODAS las filas (el UNIQUE
de la BD incluye las soft-deleted) para anticipar el 409 en vez de reventar en el flush.
"""
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.herramientas.models import Herramienta
from modules.herramientas.schemas import HerramientaCrear


class SqlHerramientasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def listar(self, *, estado: str | None = None, q: str | None = None) -> list[Herramienta]:
        """Herramientas vivas (no eliminadas) ordenadas por código; filtra por `estado` y/o `q`
        (código o nombre, ILIKE)."""
        stmt = select(Herramienta).where(Herramienta.eliminado_en.is_(None))
        if estado is not None:
            stmt = stmt.where(Herramienta.estado == estado)
        if q:
            patron = f"%{q}%"
            stmt = stmt.where(
                or_(Herramienta.codigo.ilike(patron), Herramienta.nombre.ilike(patron))
            )
        stmt = stmt.order_by(Herramienta.codigo)
        return list((await self._s.execute(stmt)).scalars().all())

    async def obtener(self, herramienta_id: int) -> Herramienta | None:
        """Herramienta viva por id (una eliminada se trata como inexistente → 404)."""
        return (
            await self._s.execute(
                select(Herramienta).where(
                    Herramienta.id == herramienta_id, Herramienta.eliminado_en.is_(None)
                )
            )
        ).scalar_one_or_none()

    async def codigo_existe(self, codigo: str, *, excluir_id: int | None = None) -> bool:
        """¿Otra herramienta ya usa este código? Mira TODAS las filas (el UNIQUE de la BD incluye las
        soft-deleted); `excluir_id` se ignora a sí mismo al editar."""
        stmt = select(Herramienta.id).where(Herramienta.codigo == codigo)
        if excluir_id is not None:
            stmt = stmt.where(Herramienta.id != excluir_id)
        return (await self._s.execute(stmt.limit(1))).first() is not None

    async def crear(self, datos: HerramientaCrear) -> Herramienta:
        herramienta = Herramienta(**datos.model_dump())
        self._s.add(herramienta)
        await self._s.flush()  # asigna herramienta.id
        return herramienta

    async def actualizar(self, herramienta: Herramienta, cambios: dict) -> Herramienta:
        """Aplica `cambios` (dict campo→valor ya validado) sobre la herramienta cargada."""
        for campo, valor in cambios.items():
            setattr(herramienta, campo, valor)
        await self._s.flush()
        return herramienta

    async def soft_delete(self, herramienta_id: int) -> bool:
        """Marca la herramienta como eliminada (`eliminado_en = ahora Colombia`). Devuelve False si no
        existe o ya estaba eliminada."""
        herramienta = await self.obtener(herramienta_id)
        if herramienta is None:
            return False
        herramienta.eliminado_en = now_co()
        await self._s.flush()
        return True
