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
        cuenta_destino: str | None = None, referencia: str | None = None,
    ) -> BancolombiaTransferencia | None:
        """Inserta una transferencia entrante venida de Gmail; None si el mensaje ya se había ingerido.

        Idempotente por `gmail_message_id` (UNIQUE, la columna de dedup de ESTE canal): `ON CONFLICT
        DO NOTHING` — reintentos del push Pub/Sub no duplican ni re-notifican. `notificado=True` porque
        el envío a Telegram lo hace la ingesta tras persistir. `naturaleza='credito'` (dinero que entra).

        `cuenta_destino` y `referencia` (la llave Bancolombia) las extrae el parser desde siempre;
        hasta 0073 se calculaban para el mensaje de Telegram y se tiraban antes de guardar.
        """
        stmt = (
            pg_insert(BancolombiaTransferencia)
            .values(
                gmail_message_id=gmail_message_id, fecha=fecha, monto=monto, remitente=remitente,
                descripcion=descripcion, tipo_transaccion=tipo_transaccion, hora=hora,
                cuenta_destino=cuenta_destino, referencia=referencia,
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

    async def listar(
        self, *, estado: str | None = None, desde: date | None = None, hasta: date | None = None,
        incluir_descartados: bool = False, limite: int = 200,
    ) -> list[BancolombiaTransferencia]:
        """Movimientos bancarios, los del extracto Y los que llegaron por el correo del banco.

        Antes filtraba `referencia_bancaria IS NOT NULL` — o sea, solo el extracto. Las filas de
        Gmail nacen con esa columna en NULL, así que desaparecían de la pantalla y del match sin que
        nadie lo notara: dos libros conviviendo en la tabla que el ADR 0028 declaró única (D1).

        `descartado_en` es el guard de "no es venta". Va aquí, en el único sitio del módulo con SQL
        (regla no negociable #2), y `sugerir_pendientes` lo hereda gratis por llamar a este método.
        """
        stmt = select(BancolombiaTransferencia)
        if estado is not None:
            stmt = stmt.where(BancolombiaTransferencia.estado_conciliacion == estado)
        if not incluir_descartados:
            stmt = stmt.where(BancolombiaTransferencia.descartado_en.is_(None))
        if desde is not None:
            stmt = stmt.where(BancolombiaTransferencia.fecha >= desde)
        if hasta is not None:
            stmt = stmt.where(BancolombiaTransferencia.fecha <= hasta)
        # El tope no es cosmético: el servicio corre una consulta de candidatos POR movimiento, así
        # que una lista sin cota es un N+1 que crece con el histórico.
        stmt = stmt.order_by(
            BancolombiaTransferencia.fecha.desc(), BancolombiaTransferencia.id.desc()
        ).limit(limite)
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

    async def marcar_descarte(
        self, mov: BancolombiaTransferencia, *, cuando: datetime | None
    ) -> BancolombiaTransferencia:
        """Sella (o quita) el "no es venta". `cuando=None` deshace.

        Al descartar hay que SOLTAR la sugerencia si la había: el filtro anti-doble-uso considera
        tomado todo interno enlazado en estado `sugerido`, así que una venta que quedara colgando de
        una transferencia descartada quedaría secuestrada — ninguna otra transferencia volvería a
        verla nunca. Es un bug de dinero silencioso.
        """
        mov.descartado_en = cuando
        if cuando is not None and mov.estado_conciliacion == "sugerido":
            mov.estado_conciliacion = "no_conciliado"
            mov.conciliado_con_tipo = None
            mov.conciliado_con_id = None
        await self._s.flush()
        return mov
