"""Repositorio de trabajadores: único lugar con SQL del módulo (regla no negociable #2).

Calca `modules.clientes.repository`. El soft delete (`eliminado_en`) marca la ocultación del registro:
`obtener`/`listar` filtran los borrados (para el API son 404 / no aparecen), pero `buscar_por_documento`
NO los filtra, porque la columna `documento` es UNIQUE en la base y abarca también las filas borradas
(así el chequeo de duplicado del servicio coincide con la constraint y no se filtra un IntegrityError).
La sesión del tenant ES la transacción; aquí no se hace commit.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.trabajadores.models import Trabajador
from modules.trabajadores.schemas import TrabajadorCrear


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
