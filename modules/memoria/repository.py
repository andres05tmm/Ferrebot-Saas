"""Repositorios de la memoria del bot: único lugar con SQL del módulo (regla #2).

Operan sobre la sesión del tenant que llega del TurnoHandler (multitenancy.md #2). Usan `flush`
(no `commit`): la sesión la commitea el context manager `tenant_session` al cerrar el request.
  - `SqlMemoriaRepository` → historial (`conversaciones_bot`) y entidades (`memoria_entidades`),
    con upsert idempotente vía `ON CONFLICT (tipo, clave)` (índice de la migración 0004).
  - `SqlCostosRepository`  → acumulación de tokens en `api_costo_diario` con `ON CONFLICT (fecha)`
    (PK=fecha; suma tokens; `modelo` = último escritor).
"""
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import now_co
from modules.memoria.models import ApiCostoDiario, ConversacionBot, MemoriaEntidad
from modules.memoria.schemas import EntidadGuardada, MensajeGuardado


class SqlMemoriaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def ultimos_mensajes(self, chat_id: int, limite: int) -> list[MensajeGuardado]:
        """Últimos `limite` mensajes del chat, devueltos en orden ASC (más antiguo primero)."""
        stmt = (
            select(ConversacionBot.rol, ConversacionBot.contenido)
            .where(ConversacionBot.chat_id == chat_id)
            .order_by(ConversacionBot.creado_en.desc(), ConversacionBot.id.desc())
            .limit(limite)
        )
        filas = (await self._s.execute(stmt)).all()
        return [MensajeGuardado(rol=rol, contenido=contenido) for rol, contenido in reversed(filas)]

    async def guardar_mensaje(self, chat_id: int, rol: str, contenido: str) -> None:
        # Savepoint: si el insert falla, revierte solo hasta aquí y re-lanza (el caller lo traga
        # best-effort) sin envenenar la transacción del turno. El flush va DENTRO del savepoint.
        async with self._s.begin_nested():
            self._s.add(ConversacionBot(chat_id=chat_id, rol=rol, contenido=contenido))
            await self._s.flush()

    async def upsert_entidad(self, tipo: str, clave: str, valor: dict) -> None:
        stmt = pg_insert(MemoriaEntidad).values(
            tipo=tipo, clave=clave, valor=valor, actualizado_en=now_co()
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tipo", "clave"],
            set_={"valor": stmt.excluded.valor, "actualizado_en": stmt.excluded.actualizado_en},
        )
        async with self._s.begin_nested():
            await self._s.execute(stmt)
            await self._s.flush()

    async def entidades_por_clave(self, clave: str) -> list[EntidadGuardada]:
        stmt = select(MemoriaEntidad.tipo, MemoriaEntidad.valor).where(MemoriaEntidad.clave == clave)
        filas = (await self._s.execute(stmt)).all()
        return [EntidadGuardada(tipo=tipo, valor=valor or {}) for tipo, valor in filas]


class SqlCostosRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def acumular(
        self, *, fecha: date, modelo: str, tokens_in: int, tokens_out: int
    ) -> None:
        """Upsert acumulativo por fecha: suma tokens; `modelo` lo pisa el último escritor."""
        stmt = pg_insert(ApiCostoDiario).values(
            fecha=fecha, modelo=modelo, tokens_in=tokens_in, tokens_out=tokens_out
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["fecha"],
            set_={
                "modelo": stmt.excluded.modelo,
                "tokens_in": func.coalesce(ApiCostoDiario.tokens_in, 0) + stmt.excluded.tokens_in,
                "tokens_out": func.coalesce(ApiCostoDiario.tokens_out, 0) + stmt.excluded.tokens_out,
            },
        )
        async with self._s.begin_nested():
            await self._s.execute(stmt)
            await self._s.flush()
