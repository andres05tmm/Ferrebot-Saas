"""Repositorio del pack postventa: único lugar con SQL (regla #2).

Los "eventos" que disparan el seguimiento se LEEN de las tablas de los otros packs (citas
cumplidas, pedidos entregados); aquí solo vive el plano de postventa (config, dedup, respuestas).
"""
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.postventa.models import EncuestaRespuesta, PostventaConfig, PostventaEnvio


class SqlPostventaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- config (una fila, get-or-create con defaults) ------------------------
    async def obtener_config(self) -> PostventaConfig:
        config = (await self._s.execute(select(PostventaConfig).limit(1))).scalar_one_or_none()
        if config is None:
            config = PostventaConfig()
            self._s.add(config)
            await self._s.flush()
        return config

    # --- eventos elegibles (lectura cruzada, sin tocar los packs) ---------------
    async def citas_para_seguimiento(
        self, *, desde: datetime, hasta: datetime, limite: int = 100
    ) -> list[dict]:
        """Citas `cumplida` cuyo fin cae en [desde, hasta) y SIN seguimiento previo (dedup)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT c.id, c.cliente_telefono AS telefono, c.cliente_nombre AS nombre "
                    "FROM citas c "
                    "WHERE c.estado = 'cumplida' AND c.fin >= :desde AND c.fin < :hasta "
                    "AND NOT EXISTS (SELECT 1 FROM postventa_envios e "
                    "                WHERE e.origen = 'cita' AND e.origen_id = c.id) "
                    "ORDER BY c.fin LIMIT :lim"
                ),
                {"desde": desde, "hasta": hasta, "lim": limite},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    async def pedidos_para_seguimiento(
        self, *, desde: datetime, hasta: datetime, limite: int = 100
    ) -> list[dict]:
        """Pedidos `entregado` actualizados en [desde, hasta) y SIN seguimiento previo (dedup)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT p.id, p.cliente_telefono AS telefono, p.cliente_nombre AS nombre "
                    "FROM pedidos p "
                    "WHERE p.estado = 'entregado' "
                    "AND p.actualizado_en >= :desde AND p.actualizado_en < :hasta "
                    "AND NOT EXISTS (SELECT 1 FROM postventa_envios e "
                    "                WHERE e.origen = 'pedido' AND e.origen_id = p.id) "
                    "ORDER BY p.actualizado_en LIMIT :lim"
                ),
                {"desde": desde, "hasta": hasta, "lim": limite},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    async def sellar_envio(self, *, origen: str, origen_id: int, telefono: str) -> None:
        """Dedup append-only: solo tras un envío exitoso (lo decide el motor)."""
        self._s.add(PostventaEnvio(origen=origen, origen_id=origen_id, telefono=telefono))
        await self._s.flush()

    # --- respuestas ---------------------------------------------------------------
    async def registrar_respuesta(
        self, *, telefono: str, calificacion: int, comentario: str | None
    ) -> EncuestaRespuesta:
        respuesta = EncuestaRespuesta(
            telefono=telefono, calificacion=calificacion, comentario=comentario
        )
        self._s.add(respuesta)
        await self._s.flush()
        await publish(self._s, "encuesta_respondida", {
            "respuesta_id": respuesta.id, "calificacion": calificacion,
        })
        return respuesta

    async def listar_respuestas(self, *, limite: int = 200) -> list[EncuestaRespuesta]:
        return list(
            (
                await self._s.execute(
                    select(EncuestaRespuesta)
                    .order_by(EncuestaRespuesta.creado_en.desc())
                    .limit(limite)
                )
            ).scalars()
        )

    async def satisfaccion(self) -> dict:
        """KPI del dueño: promedio y conteo de calificaciones."""
        fila = (
            await self._s.execute(
                text(
                    "SELECT COALESCE(ROUND(AVG(calificacion), 2), 0) AS promedio, "
                    "COUNT(*) AS respuestas FROM encuestas_respuestas"
                )
            )
        ).first()
        return {"promedio": float(fila.promedio), "respuestas": int(fila.respuestas)}
