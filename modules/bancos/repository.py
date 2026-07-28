"""Repositorio de conciliación bancaria: único lugar con SQL del módulo (regla no negociable #2).

La sesión del tenant es la transacción y la frontera del aislamiento. Dos responsabilidades:

1. Ingesta idempotente del extracto → `bancolombia_transferencias` (INSERT ... ON CONFLICT DO NOTHING
   sobre el índice UNIQUE parcial de `referencia_bancaria`): reprocesar el mismo extracto no duplica.
2. Match contra movimientos internos por monto+fecha, acotado por `naturaleza` — crédito: ventas por
   transferencia, la parte transferencia de las mixtas y abonos de fiado; débito: gastos y abonos a
   proveedores —, EXCLUYENDO los internos ya enlazados por otro movimiento bancario. El
   enlace (sugerido/conciliado) SOLO escribe columnas de estado en la fila bancaria: no toca saldos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config.timezone import rango_dia_co
from modules.bancos.models import BancolombiaTransferencia

# Movimientos internos candidatos por naturaleza del movimiento bancario. Cada consulta devuelve
# (tipo, id, monto, fecha, cliente, descripcion) y EXCLUYE los ya enlazados (sugerido/conciliado) por
# OTRA fila bancaria, para no ofrecer el mismo interno dos veces — pero SÍ mantiene el candidato que
# la propia fila (`self_id`) ya tenía sugerido, para confirmarlo.
#
# El día es una ventana de instantes (`:inicio`/`:fin` de `rango_dia_co`) y no `columna::date = :fecha`:
# el cast depende del `TimeZone` de la sesión de Postgres, así que un pago de las 7 p. m. caía en el
# día siguiente y dejaba de calzar con su venta (regla no negociable #4).


def _no_enlazado(tipo: str, id_expr: str) -> str:
    """Filtro anti-doble-uso para una rama del UNION. `tipo`/`id_expr` son literales de este módulo
    (nunca entrada de usuario); el monto y la fecha sí van como parámetros."""
    return (
        f"AND NOT EXISTS (SELECT 1 FROM bancolombia_transferencias bt "
        f"WHERE bt.conciliado_con_tipo = '{tipo}' AND bt.conciliado_con_id = {id_expr} "
        "AND bt.estado_conciliacion IN ('sugerido', 'conciliado') "
        "AND bt.id IS DISTINCT FROM :self_id) "
    )


_DIA_CO = "AT TIME ZONE 'America/Bogota'"

# Un crédito puede ser el cobro de una venta entera, la PARTE por transferencia de una venta mixta,
# o el abono de un cliente a su fiado. Las tres se ofrecen juntas y la regla dura del ADR 0028 sigue
# igual: si el monto calza con más de una, decide una persona.
_CANDIDATOS_CREDITO = (
    "SELECT 'venta' AS tipo, x.id, x.total AS monto, "
    f"       (x.fecha {_DIA_CO})::date AS fecha, c.nombre AS cliente, "
    "       'venta #' || x.consecutivo AS descripcion "
    "FROM ventas x LEFT JOIN clientes c ON c.id = x.cliente_id "
    "WHERE x.metodo_pago = 'transferencia' AND x.estado = 'completada' "
    "AND x.total = :monto AND x.fecha BETWEEN :inicio AND :fin "
    + _no_enlazado("venta", "x.id")
    # Venta MIXTA (0053): calza contra el monto de SU parte por transferencia, no contra el total.
    # El enlace sigue siendo la venta (`tipo='venta'`): no se inventa un tipo para media venta.
    + "UNION ALL "
    "SELECT 'venta' AS tipo, v.id, p.monto, "
    f"       (v.fecha {_DIA_CO})::date AS fecha, c.nombre AS cliente, "
    "       'parte por transferencia de la venta #' || v.consecutivo AS descripcion "
    "FROM ventas_pagos p JOIN ventas v ON v.id = p.venta_id "
    "LEFT JOIN clientes c ON c.id = v.cliente_id "
    # `v.metodo_pago = 'mixto'` deja las dos primeras ramas disjuntas por construcción: sin eso, una
    # venta que tuviera fila en las dos se ofrecería duplicada y el match la leería como ambigua.
    "WHERE p.metodo = 'transferencia' AND v.metodo_pago = 'mixto' AND v.estado = 'completada' "
    "AND p.monto = :monto AND v.fecha BETWEEN :inicio AND :fin "
    + _no_enlazado("venta", "v.id")
    # Abono a un fiado: el cliente que debía y paga por transferencia. 'abono_fiado' y no 'abono',
    # que es el pago a un PROVEEDOR (`facturas_abonos`): compartir el nombre cruzaría los ids de dos
    # tablas distintas en el filtro anti-doble-uso.
    + "UNION ALL "
    "SELECT 'abono_fiado' AS tipo, m.id, m.monto, "
    f"       (m.creado_en {_DIA_CO})::date AS fecha, c.nombre AS cliente, "
    "       'abono al fiado #' || f.id AS descripcion "
    "FROM fiados_movimientos m JOIN fiados f ON f.id = m.fiado_id "
    "LEFT JOIN clientes c ON c.id = f.cliente_id "
    "WHERE m.tipo = 'abono' AND m.monto = :monto AND m.creado_en BETWEEN :inicio AND :fin "
    + _no_enlazado("abono_fiado", "m.id")
)

_CANDIDATOS_DEBITO = (
    "SELECT 'gasto' AS tipo, x.id, x.monto AS monto, "
    f"       (x.creado_en {_DIA_CO})::date AS fecha, NULL::text AS cliente, "
    "       x.concepto AS descripcion "
    "FROM gastos x "
    "WHERE x.monto = :monto AND x.creado_en BETWEEN :inicio AND :fin AND x.anulado_en IS NULL "
    + _no_enlazado("gasto", "x.id")
    # `facturas_abonos.fecha` es DATE (el día que el dueño registró el pago): sin instantes que acotar.
    + "UNION ALL "
    "SELECT 'abono' AS tipo, x.id, x.monto AS monto, x.fecha AS fecha, NULL::text AS cliente, "
    "       'abono factura ' || x.factura_id AS descripcion "
    "FROM facturas_abonos x "
    "WHERE x.monto = :monto AND x.fecha = :fecha "
    + _no_enlazado("abono", "x.id")
)


# Cuánta plata entró, por cuenta. Solo CRÉDITOS: los egresos de estas cuentas se mezclan con gastos
# personales y de la casa, así que no se llevan (decisión del dueño).
#
# `total` es todo lo que entró; `total_negocio` descuenta lo marcado "no es venta". `sin_clasificar`
# cuenta los que siguen sin resolverse (ni descartados ni conciliados): es la deuda de clasificación
# que hay detrás del número, y sin ella `total_negocio` se lee como más firme de lo que es.
_TOTALES_POR_CUENTA = (
    "SELECT cuenta_destino AS cuenta, count(*) AS movimientos, "
    "       COALESCE(SUM(monto), 0) AS total, "
    "       COALESCE(SUM(monto) FILTER (WHERE descartado_en IS NULL), 0) AS total_negocio, "
    "       count(*) FILTER (WHERE descartado_en IS NULL "
    "                        AND estado_conciliacion <> 'conciliado') AS sin_clasificar "
    "FROM bancolombia_transferencias "
    "WHERE naturaleza = 'credito' AND fecha BETWEEN :desde AND :hasta "
    "GROUP BY cuenta_destino "
    "ORDER BY total DESC"
)


# Quién repite. Agrupa por el nombre que trae el correo del banco, normalizado a mayúsculas y sin
# espacios sobrantes. NO toca `clientes`: es un reporte de lectura, no un alta de clientes.
#
# Los descartados quedan fuera: la plata de la casa no es un cliente fiel. La agrupación es exacta
# (sin `unaccent` ni `pg_trgm`), así que "JUAN PÉREZ" y "JUAN PEREZ" cuentan aparte; si el dueño ve
# duplicados por tildes, ahí se agrega — antes es complejidad sin evidencia.
_REMITENTES = (
    "SELECT upper(trim(remitente)) AS nombre, count(*) AS veces, "
    "       COALESCE(SUM(monto), 0) AS total, min(fecha) AS primera, max(fecha) AS ultima, "
    "       count(*) FILTER (WHERE estado_conciliacion = 'conciliado') AS conciliados "
    "FROM bancolombia_transferencias "
    "WHERE naturaleza = 'credito' AND descartado_en IS NULL "
    "AND remitente IS NOT NULL AND trim(remitente) <> '' "
    "AND fecha BETWEEN :desde AND :hasta "
    "GROUP BY upper(trim(remitente)) "
    "HAVING count(*) >= :min_veces "
    "ORDER BY veces DESC, total DESC "
    "LIMIT :limite"
)


@dataclass(frozen=True, slots=True)
class RemitenteRecurrente:
    nombre: str
    veces: int
    total: Decimal
    primera: date
    ultima: date
    conciliados: int


@dataclass(frozen=True, slots=True)
class TotalCuenta:
    cuenta: str | None          # None = el parser no pudo leerla; se muestra, no se esconde
    movimientos: int
    total: Decimal
    total_negocio: Decimal
    sin_clasificar: int


@dataclass(frozen=True, slots=True)
class Candidato:
    tipo: str
    id: int
    monto: Decimal
    fecha: date
    descripcion: str | None
    # Quién es, para que la decisión humana sobre un ambiguo sea informada. None = venta sin cliente.
    cliente: str | None = None


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

    async def totales_por_cuenta(self, *, desde: date, hasta: date) -> list[TotalCuenta]:
        """Ingresos del período agrupados por cuenta destino, de mayor a menor."""
        filas = (
            await self._s.execute(text(_TOTALES_POR_CUENTA), {"desde": desde, "hasta": hasta})
        ).all()
        return [
            TotalCuenta(
                cuenta=f.cuenta, movimientos=f.movimientos, total=Decimal(f.total),
                total_negocio=Decimal(f.total_negocio), sin_clasificar=f.sin_clasificar,
            )
            for f in filas
        ]

    async def remitentes_recurrentes(
        self, *, desde: date, hasta: date, min_veces: int, limite: int
    ) -> list[RemitenteRecurrente]:
        """Quién mandó plata más de una vez en el período, de mayor a menor frecuencia."""
        filas = (
            await self._s.execute(
                text(_REMITENTES),
                {"desde": desde, "hasta": hasta, "min_veces": min_veces, "limite": limite},
            )
        ).all()
        return [
            RemitenteRecurrente(
                nombre=f.nombre, veces=f.veces, total=Decimal(f.total),
                primera=f.primera, ultima=f.ultima, conciliados=f.conciliados,
            )
            for f in filas
        ]

    async def candidatos(
        self, *, monto: Decimal, fecha: date, naturaleza: str, excluir_mov_id: int | None = None
    ) -> list[Candidato]:
        """Movimientos internos que calzan por monto+fecha (acotado por naturaleza), no enlazados.

        `excluir_mov_id` = el propio movimiento bancario: su candidato ya sugerido NO se descarta
        (para poder confirmarlo); los tomados por OTRAS filas bancarias sí.

        El día del banco se traduce a la ventana [00:00, 23:59:59.999999] de ese día en Colombia.
        """
        sql = _CANDIDATOS_CREDITO if naturaleza == "credito" else _CANDIDATOS_DEBITO
        inicio, fin = rango_dia_co(fecha, fecha)
        params = {
            "monto": monto, "fecha": fecha, "inicio": inicio, "fin": fin, "self_id": excluir_mov_id,
        }
        filas = (await self._s.execute(text(sql), params)).all()
        return [
            Candidato(tipo=f.tipo, id=f.id, monto=Decimal(f.monto), fecha=f.fecha,
                      descripcion=f.descripcion, cliente=f.cliente)
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
