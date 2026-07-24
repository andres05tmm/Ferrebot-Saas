"""Repositorio de la cola de impresión (ADR 0033 D2–D3). Acceso a datos SOLO por aquí."""
import json

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.impresion.models import TrabajoImpresion

# Un trabajo `entregado_agente` sin ack en esta ventana se considera perdido (corte de conexión)
# y la cola lo re-entrega. El registro local del agente + el UNIQUE evitan el papel doble.
VENCIMIENTO_ENTREGA_SEG = 120


class SqlImpresionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @property
    def sesion(self) -> AsyncSession:
        """La sesión del tenant (la usa el servicio para armar payloads bajo demanda)."""
        return self._s

    async def por_id(self, trabajo_id: int) -> TrabajoImpresion | None:
        return await self._s.get(TrabajoImpresion, trabajo_id)

    async def por_key(self, idempotency_key: str) -> TrabajoImpresion | None:
        return (
            await self._s.execute(
                select(TrabajoImpresion).where(TrabajoImpresion.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()

    async def crear(
        self, *, tipo: str, payload: dict, idempotency_key: str, zona_id: int | None = None,
        pedido_id: int | None = None, comanda_id: int | None = None, venta_id: int | None = None,
        reimpresion_de: int | None = None,
    ) -> TrabajoImpresion:
        """INSERT idempotente por clave UNIQUE: en replay devuelve el trabajo EXISTENTE."""
        nuevo = (
            await self._s.execute(
                text(
                    "INSERT INTO trabajos_impresion (tipo, payload, zona_id, pedido_id, "
                    "comanda_id, venta_id, reimpresion_de, idempotency_key) "
                    "VALUES (:t, :pl, :z, :p, :c, :v, :r, :key) "
                    "ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"
                ),
                {
                    "t": tipo, "pl": json.dumps(payload, ensure_ascii=False), "z": zona_id,
                    "p": pedido_id, "c": comanda_id, "v": venta_id, "r": reimpresion_de,
                    "key": idempotency_key,
                },
            )
        ).scalar_one_or_none()
        if nuevo is not None:
            await publish(self._s, "impresion_trabajo", {"trabajos": [nuevo]})
            trabajo = await self.por_id(nuevo)
        else:
            trabajo = await self.por_key(idempotency_key)   # replay
        assert trabajo is not None
        return trabajo

    async def reclamar_cola(self, *, limite: int = 50) -> list[TrabajoImpresion]:
        """Entrega atómica: pendientes + entregados VENCIDOS pasan a `entregado_agente` y salen.

        `FOR UPDATE SKIP LOCKED` serializa agentes concurrentes sin bloquear la cola.
        """
        ids = [
            f.id for f in (
                await self._s.execute(
                    text(
                        "UPDATE trabajos_impresion SET estado = 'entregado_agente', "
                        "entregado_en = now(), intentos = intentos + 1 "
                        "WHERE id IN ("
                        "  SELECT id FROM trabajos_impresion "
                        "  WHERE estado = 'pendiente' OR (estado = 'entregado_agente' "
                        "        AND entregado_en < now() - make_interval(secs => :venc)) "
                        "  ORDER BY id LIMIT :lim FOR UPDATE SKIP LOCKED"
                        ") RETURNING id"
                    ),
                    {"lim": limite, "venc": VENCIMIENTO_ENTREGA_SEG},
                )
            ).all()
        ]
        if not ids:
            return []
        return list(
            (
                await self._s.execute(
                    select(TrabajoImpresion)
                    .where(TrabajoImpresion.id.in_(ids))
                    .order_by(TrabajoImpresion.id)
                )
            ).scalars()
        )

    async def ack(self, trabajo: TrabajoImpresion, *, ok: bool, detalle: str | None) -> TrabajoImpresion:
        if ok:
            trabajo.estado = "impreso"
            await self._s.execute(
                text("UPDATE trabajos_impresion SET impreso_en = now() WHERE id = :t"),
                {"t": trabajo.id},
            )
        else:
            trabajo.estado = "error"
            trabajo.error_detalle = detalle
        await self._s.flush()
        await publish(self._s, "impresion_trabajo", {
            "trabajos": [trabajo.id], "estado": trabajo.estado,
        })
        return trabajo

    async def contar_reimpresiones_cerradas(self, original_id: int) -> int:
        """Reimpresiones TERMINALES (impreso|error) del original — numera la clave de la próxima.

        Una reimpresión aún viva (pendiente/entregada) hace colisionar la clave del doble clic:
        se devuelve la existente en vez de encolar papel de más.
        """
        return (
            await self._s.execute(
                text(
                    "SELECT count(*) FROM trabajos_impresion "
                    "WHERE reimpresion_de = :o AND estado IN ('impreso', 'error')"
                ),
                {"o": original_id},
            )
        ).scalar_one()
