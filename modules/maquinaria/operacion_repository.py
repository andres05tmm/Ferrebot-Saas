"""Repositorio de la operación de máquina EN VIVO (sesiones + tramos, migración 0055).

Único lugar con SQL de la operación en vivo (regla #2). La sesión del tenant ES la transacción y la
frontera del aislamiento (sin `empresa_id`). Emite eventos SSE (`sesion_maquina_iniciada`,
`tramo_operador_rotado`, `sesion_maquina_finalizada`) que consume el tablero en vivo del dashboard; el
NOTIFY sale al commit del llamador. Nombres de obra/operador se resuelven por JOIN (N+1-free).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish
from modules.maquinaria.models import SesionMaquina, TramoOperador


class SqlOperacionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ---- Sesiones ---------------------------------------------------------------------------------
    async def crear_sesion(
        self,
        *,
        maquina_id: int,
        obra_id: int,
        asignacion_id: int,
        fecha: date,
        iniciada_en: datetime,
    ) -> SesionMaquina:
        """Abre la sesión de operación (estado ABIERTA) y hace flush (asigna `id`). Emite el evento SSE."""
        sesion = SesionMaquina(
            maquina_id=maquina_id,
            obra_id=obra_id,
            asignacion_id=asignacion_id,
            fecha=fecha,
            estado="ABIERTA",
            iniciada_en=iniciada_en,
        )
        self._s.add(sesion)
        await self._s.flush()  # asigna sesion.id
        await self._publicar(sesion, "sesion_maquina_iniciada")
        return sesion

    async def sesion_abierta_de_maquina(self, maquina_id: int) -> SesionMaquina | None:
        """Sesión ABIERTA de la máquina, si la hay (a lo sumo una: índice único parcial en la BD)."""
        return (
            await self._s.execute(
                select(SesionMaquina).where(
                    SesionMaquina.maquina_id == maquina_id,
                    SesionMaquina.estado == "ABIERTA",
                )
            )
        ).scalar_one_or_none()

    async def obtener_sesion(self, sesion_id: int) -> SesionMaquina | None:
        return (
            await self._s.execute(select(SesionMaquina).where(SesionMaquina.id == sesion_id))
        ).scalar_one_or_none()

    async def finalizar_sesion(
        self, sesion: SesionMaquina, *, finalizada_en: datetime, registro_horas_id: int
    ) -> SesionMaquina:
        """Marca la sesión FINALIZADA y enlaza el parte materializado (`registro_horas_id`)."""
        sesion.estado = "FINALIZADA"
        sesion.finalizada_en = finalizada_en
        sesion.registro_horas_id = registro_horas_id
        await self._s.flush()
        await self._publicar(sesion, "sesion_maquina_finalizada")
        return sesion

    async def anular_sesion(
        self, sesion: SesionMaquina, *, finalizada_en: datetime
    ) -> SesionMaquina:
        """Marca la sesión ANULADA (descarta la captura; no materializa ni factura)."""
        sesion.estado = "ANULADA"
        sesion.finalizada_en = finalizada_en
        await self._s.flush()
        await self._publicar(sesion, "sesion_maquina_finalizada")
        return sesion

    async def publicar_rotacion(self, sesion: SesionMaquina) -> None:
        """Evento SSE de rotación de operador (el tablero en vivo refresca el operador actual)."""
        await self._publicar(sesion, "tramo_operador_rotado")

    async def _publicar(self, sesion: SesionMaquina, evento: str) -> None:
        await publish(
            self._s,
            evento,
            {
                "sesion_id": sesion.id,
                "maquina_id": sesion.maquina_id,
                "obra_id": sesion.obra_id,
                "estado": sesion.estado,
            },
        )

    # ---- Tramos de operador (franjas en vivo) -----------------------------------------------------
    async def abrir_tramo(
        self, *, sesion_id: int, operador_id: int | None, iniciado_en: datetime
    ) -> TramoOperador:
        """Abre un tramo (finalizado_en NULL = corriendo) y hace flush. Un solo abierto por sesión
        (índice único parcial): el caller cierra el anterior antes de abrir en una rotación."""
        tramo = TramoOperador(
            sesion_id=sesion_id, operador_id=operador_id, iniciado_en=iniciado_en
        )
        self._s.add(tramo)
        await self._s.flush()  # asigna tramo.id
        return tramo

    async def tramo_abierto(self, sesion_id: int) -> TramoOperador | None:
        """El tramo corriendo de la sesión (finalizado_en NULL), si lo hay."""
        return (
            await self._s.execute(
                select(TramoOperador).where(
                    TramoOperador.sesion_id == sesion_id,
                    TramoOperador.finalizado_en.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def cerrar_tramo(self, tramo: TramoOperador, *, finalizado_en: datetime) -> None:
        """Cierra el tramo (fija `finalizado_en`); las horas se confirman aparte al finalizar."""
        tramo.finalizado_en = finalizado_en
        await self._s.flush()

    async def tramos_de_sesion(self, sesion_id: int) -> list[TramoOperador]:
        """Tramos de la sesión ordenados por inicio (para materializar y para el desglose de rotación)."""
        stmt = (
            select(TramoOperador)
            .where(TramoOperador.sesion_id == sesion_id)
            .order_by(TramoOperador.iniciado_en, TramoOperador.id)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def tramos_detalle(self, sesion_id: int) -> list[dict]:
        """Tramos de la sesión con el nombre del operador resuelto (LEFT JOIN trabajadores), ordenados por
        inicio. Lo consume el modal de revisión al finalizar (horas propuestas por tramo)."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT tr.id, tr.operador_id, "
                    "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') "
                    "    AS operador, "
                    "  tr.iniciado_en, tr.finalizado_en, tr.horas_confirmadas "
                    "FROM tramos_operador tr "
                    "LEFT JOIN trabajadores t ON t.id = tr.operador_id "
                    "WHERE tr.sesion_id = :s ORDER BY tr.iniciado_en, tr.id"
                ),
                {"s": sesion_id},
            )
        ).all()
        return [dict(f._mapping) for f in filas]

    async def fijar_horas_confirmadas(self, tramo: TramoOperador, horas: Decimal) -> None:
        """Guarda las horas confirmadas del tramo (default = medido por el reloj; el humano las ajusta)."""
        tramo.horas_confirmadas = horas
        await self._s.flush()

    # ---- Tablero en vivo --------------------------------------------------------------------------
    async def tablero(self) -> list[dict]:
        """Sesiones ABIERTAS con nombres de máquina/obra + el operador y el inicio del tramo corriente.

        Una consulta (N+1-free): LEFT JOIN LATERAL al tramo abierto de cada sesión para el operador
        actual y desde cuándo. Ordenadas por inicio de sesión. Alimenta las tarjetas con cronómetro."""
        filas = (
            await self._s.execute(
                text(
                    "SELECT s.id AS sesion_id, s.maquina_id, m.nombre AS maquina, "
                    "  s.obra_id, o.nombre AS obra, s.iniciada_en, "
                    "  tr.operador_id, "
                    "  NULLIF(TRIM(COALESCE(t.nombres,'') || ' ' || COALESCE(t.apellidos,'')), '') "
                    "    AS operador, "
                    "  tr.iniciado_en AS tramo_desde "
                    "FROM sesiones_maquina s "
                    "JOIN maquinas m ON m.id = s.maquina_id "
                    "JOIN obras o ON o.id = s.obra_id "
                    "LEFT JOIN LATERAL ("
                    "  SELECT operador_id, iniciado_en FROM tramos_operador "
                    "  WHERE sesion_id = s.id AND finalizado_en IS NULL "
                    "  ORDER BY iniciado_en DESC LIMIT 1"
                    ") tr ON true "
                    "LEFT JOIN trabajadores t ON t.id = tr.operador_id "
                    "WHERE s.estado = 'ABIERTA' "
                    "ORDER BY s.iniciada_en, s.id"
                )
            )
        ).all()
        return [dict(f._mapping) for f in filas]
