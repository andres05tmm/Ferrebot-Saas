"""Repositorio de conciliación bancaria: único lugar con SQL del módulo (regla no negociable #2).

La sesión del tenant es la transacción y la frontera del aislamiento. Dos responsabilidades:

1. Ingesta idempotente del extracto → `bancolombia_transferencias` (INSERT ... ON CONFLICT DO NOTHING
   sobre el índice UNIQUE parcial de `referencia_bancaria`): reprocesar el mismo extracto no duplica.
2. Match contra movimientos internos (ventas por transferencia / gastos / abonos) por monto+fecha,
   acotado por `naturaleza`, EXCLUYENDO los internos ya enlazados por otro movimiento bancario. El
   enlace (sugerido/conciliado) SOLO escribe columnas de estado en la fila bancaria: no toca saldos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.bancos.models import BancolombiaTransferencia

# Movimientos internos candidatos por naturaleza del movimiento bancario. Cada consulta devuelve
# (tipo, id, monto, fecha, descripcion) y EXCLUYE los ya enlazados (sugerido/conciliado) por OTRA
# fila bancaria (`bt.id IS DISTINCT FROM :self_id`), para no ofrecer el mismo interno dos veces —
# pero SÍ mantiene el candidato que la propia fila (`self_id`) ya tenía sugerido, para confirmarlo.
_NO_ENLAZADO = (
    "AND NOT EXISTS (SELECT 1 FROM bancolombia_transferencias bt "
    "WHERE bt.conciliado_con_tipo = :tipo AND bt.conciliado_con_id = x.id "
    "AND bt.estado_conciliacion IN ('sugerido', 'conciliado') "
    "AND bt.id IS DISTINCT FROM :self_id) "
)

_CANDIDATOS_CREDITO = (
    "SELECT 'venta' AS tipo, x.id, x.total AS monto, x.fecha::date AS fecha, "
    "       'venta #' || x.consecutivo AS descripcion "
    "FROM ventas x "
    "WHERE x.metodo_pago = 'transferencia' AND x.estado = 'completada' "
    "AND x.total = :monto AND x.fecha::date = :fecha "
    + _NO_ENLAZADO.replace(":tipo", "'venta'")
)

_CANDIDATOS_DEBITO = (
    "SELECT 'gasto' AS tipo, x.id, x.monto AS monto, x.creado_en::date AS fecha, x.concepto AS descripcion "
    "FROM gastos x "
    "WHERE x.monto = :monto AND x.creado_en::date = :fecha AND x.anulado_en IS NULL "
    + _NO_ENLAZADO.replace(":tipo", "'gasto'")
    + "UNION ALL "
    "SELECT 'abono' AS tipo, x.id, x.monto AS monto, x.fecha AS fecha, "
    "       'abono factura ' || x.factura_id AS descripcion "
    "FROM facturas_abonos x "
    "WHERE x.monto = :monto AND x.fecha = :fecha "
    + _NO_ENLAZADO.replace(":tipo", "'abono'")
)


@dataclass(frozen=True, slots=True)
class Candidato:
    tipo: str
    id: int
    monto: Decimal
    fecha: date
    descripcion: str | None


class SqlBancosRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # --- ingesta idempotente del extracto ------------------------------------
    async def ingestar_uno(
        self, *, referencia_bancaria: str, fecha: date, monto: Decimal,
        naturaleza: str, descripcion: str | None, remitente: str | None,
    ) -> bool:
        """Inserta una línea; devuelve True si se insertó, False si ya existía (misma referencia).

        `ON CONFLICT DO NOTHING` sobre el índice UNIQUE parcial de `referencia_bancaria`: idempotente
        aun bajo reintentos/concurrencia (no es un check-then-insert con ventana de carrera).
        """
        stmt = (
            pg_insert(BancolombiaTransferencia)
            .values(
                referencia_bancaria=referencia_bancaria, fecha=fecha, monto=monto,
                naturaleza=naturaleza, descripcion=descripcion, remitente=remitente,
                notificado=False, estado_conciliacion="no_conciliado",
            )
            .on_conflict_do_nothing(
                index_elements=["referencia_bancaria"],
                index_where=BancolombiaTransferencia.referencia_bancaria.isnot(None),
            )
        )
        res = await self._s.execute(stmt)
        await self._s.flush()
        return res.rowcount == 1

    async def ingestar_gmail(
        self, *, gmail_message_id: str, fecha: date, monto: Decimal, remitente: str | None,
        descripcion: str | None, tipo_transaccion: str | None, hora: str | None,
    ) -> BancolombiaTransferencia | None:
        """Inserta una transferencia entrante venida de Gmail; None si el mensaje ya se había ingerido.

        Idempotente por `gmail_message_id` (UNIQUE, la columna de dedup de ESTE canal): `ON CONFLICT
        DO NOTHING` — reintentos del push Pub/Sub no duplican ni re-notifican. `notificado=True` porque
        el envío a Telegram lo hace la ingesta tras persistir. `naturaleza='credito'` (dinero que entra).
        """
        stmt = (
            pg_insert(BancolombiaTransferencia)
            .values(
                gmail_message_id=gmail_message_id, fecha=fecha, monto=monto, remitente=remitente,
                descripcion=descripcion, tipo_transaccion=tipo_transaccion, hora=hora,
                naturaleza="credito", estado_conciliacion="no_conciliado", notificado=True,
            )
            .on_conflict_do_nothing(index_elements=["gmail_message_id"])
            .returning(BancolombiaTransferencia)
        )
        mov = (await self._s.execute(stmt)).scalar_one_or_none()
        await self._s.flush()
        return mov

    # --- lectura -------------------------------------------------------------
    async def obtener(self, mov_id: int) -> BancolombiaTransferencia | None:
        return (
            await self._s.execute(
                select(BancolombiaTransferencia).where(BancolombiaTransferencia.id == mov_id)
            )
        ).scalar_one_or_none()

    async def listar(self, *, estado: str | None = None) -> list[BancolombiaTransferencia]:
        stmt = select(BancolombiaTransferencia).where(
            BancolombiaTransferencia.referencia_bancaria.isnot(None)   # solo movimientos del extracto
        )
        if estado is not None:
            stmt = stmt.where(BancolombiaTransferencia.estado_conciliacion == estado)
        stmt = stmt.order_by(
            BancolombiaTransferencia.fecha.desc(), BancolombiaTransferencia.id.desc()
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def candidatos(
        self, *, monto: Decimal, fecha: date, naturaleza: str, excluir_mov_id: int | None = None
    ) -> list[Candidato]:
        """Movimientos internos que calzan por monto+fecha (acotado por naturaleza), no enlazados.

        `excluir_mov_id` = el propio movimiento bancario: su candidato ya sugerido NO se descarta
        (para poder confirmarlo); los tomados por OTRAS filas bancarias sí.
        """
        sql = _CANDIDATOS_CREDITO if naturaleza == "credito" else _CANDIDATOS_DEBITO
        params = {"monto": monto, "fecha": fecha, "self_id": excluir_mov_id}
        filas = (await self._s.execute(text(sql), params)).all()
        return [
            Candidato(tipo=f.tipo, id=f.id, monto=Decimal(f.monto), fecha=f.fecha,
                      descripcion=f.descripcion)
            for f in filas
        ]

    # --- transiciones de estado (SOLO escriben la fila bancaria: no tocan saldos) --------
    async def marcar_sugerido(
        self, mov: BancolombiaTransferencia, *, tipo: str, id_interno: int
    ) -> BancolombiaTransferencia:
        mov.estado_conciliacion = "sugerido"
        mov.conciliado_con_tipo = tipo
        mov.conciliado_con_id = id_interno
        mov.conciliado_en = None
        await self._s.flush()
        return mov

    async def confirmar(
        self, mov: BancolombiaTransferencia, *, tipo: str, id_interno: int, cuando: datetime
    ) -> BancolombiaTransferencia:
        mov.estado_conciliacion = "conciliado"
        mov.conciliado_con_tipo = tipo
        mov.conciliado_con_id = id_interno
        mov.conciliado_en = cuando
        await self._s.flush()
        return mov
