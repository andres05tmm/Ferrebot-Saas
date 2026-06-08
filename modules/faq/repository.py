"""Repositorio del pack FAQ / conocimiento: único lugar con SQL del módulo (regla #2).

CRUD de las entradas de conocimiento + `listar` (todas o solo activas) que consume el recuperador.
`actualizar` sella `actualizado_en` con un datetime CONCRETO en hora Colombia (no `func.now()`, que
dejaría el atributo expirado y dispararía un lazy-load async al serializar la respuesta) y refresca la
fila para concretar los server_default. El motor (`service.py`) consume este puerto y nunca escribe SQL.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import COLOMBIA_TZ
from modules.faq.models import Conocimiento
from modules.faq.schemas import ConocimientoCrear


class ConocimientoRepo(Protocol):
    """Puerto de datos del pack (lo implementa `SqlConocimientoRepository`; los tests lo falsean)."""

    async def listar(self, *, solo_activas: bool = True) -> list[Conocimiento]: ...
    async def por_id(self, conocimiento_id: int) -> Conocimiento | None: ...
    async def crear(self, datos: ConocimientoCrear) -> Conocimiento: ...
    async def actualizar(self, entrada: Conocimiento, datos: ConocimientoCrear) -> Conocimiento: ...
    async def eliminar(self, entrada: Conocimiento) -> None: ...


class SqlConocimientoRepository:
    """Implementación SQL del puerto sobre la sesión del tenant (regla de multitenancy #2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def listar(self, *, solo_activas: bool = True) -> list[Conocimiento]:
        """Entradas ordenadas por `orden` (y título como desempate). Por defecto solo las activas."""
        stmt = select(Conocimiento).order_by(Conocimiento.orden, Conocimiento.titulo)
        if solo_activas:
            stmt = stmt.where(Conocimiento.activo.is_(True))
        return list((await self._s.execute(stmt)).scalars().all())

    async def por_id(self, conocimiento_id: int) -> Conocimiento | None:
        return await self._s.get(Conocimiento, conocimiento_id)

    async def crear(self, datos: ConocimientoCrear) -> Conocimiento:
        entrada = Conocimiento(**datos.model_dump())
        self._s.add(entrada)
        await self._s.flush()
        await self._s.refresh(entrada)  # concreta server_default (creado_en) para la serialización
        return entrada

    async def actualizar(self, entrada: Conocimiento, datos: ConocimientoCrear) -> Conocimiento:
        for campo, valor in datos.model_dump().items():
            setattr(entrada, campo, valor)
        entrada.actualizado_en = datetime.now(COLOMBIA_TZ)  # valor concreto, no expresión SQL diferida
        await self._s.flush()
        await self._s.refresh(entrada)
        return entrada

    async def eliminar(self, entrada: Conocimiento) -> None:
        await self._s.delete(entrada)
        await self._s.flush()
