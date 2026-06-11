"""Repositorio de cobros: único lugar con SQL del frente de pagos (regla #2)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.pagos.models import Cobro


class SqlPagosRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def cobro_por_referencia(self, referencia: str) -> Cobro | None:
        return (
            await self._s.execute(select(Cobro).where(Cobro.referencia == referencia))
        ).scalar_one_or_none()

    async def cobro_por_origen(self, origen: str, origen_id: int) -> Cobro | None:
        return (
            await self._s.execute(
                select(Cobro).where(Cobro.origen == origen, Cobro.origen_id == origen_id)
            )
        ).scalar_one_or_none()

    async def cobro_por_id(self, cobro_id: int) -> Cobro | None:
        return await self._s.get(Cobro, cobro_id)

    async def crear(self, cobro: Cobro) -> Cobro:
        self._s.add(cobro)
        await self._s.flush()
        await publish(self._s, "cobro_creado", {
            "cobro_id": cobro.id, "origen": cobro.origen, "monto": str(cobro.monto),
        })
        return cobro

    async def marcar(self, cobro: Cobro, estado: str) -> Cobro:
        cobro.estado = estado
        await self._s.flush()
        await self._s.refresh(cobro, attribute_names=["actualizado_en"])   # onupdate lo expiró
        evento = "cobro_pagado" if estado == "pagado" else "cobro_estado"
        await publish(self._s, evento, {"cobro_id": cobro.id, "estado": estado})
        return cobro

    async def pendientes_de(self, proveedor: str, *, limite: int = 100) -> list[Cobro]:
        """Cobros del proveedor aún pendientes (los barre la conciliación del worker)."""
        return list(
            (
                await self._s.execute(
                    select(Cobro)
                    .where(Cobro.estado == "pendiente", Cobro.proveedor == proveedor)
                    .order_by(Cobro.id)
                    .limit(limite)
                )
            ).scalars()
        )

    async def listar(self, *, estados: list[str] | None = None, limite: int = 200) -> list[Cobro]:
        consulta = select(Cobro).order_by(Cobro.creado_en.desc()).limit(limite)
        if estados:
            consulta = consulta.where(Cobro.estado.in_(estados))
        return list((await self._s.execute(consulta)).scalars())
