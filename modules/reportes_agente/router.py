"""Analítica del dueño (plan Ola 3 §11): el reporte del AGENTE — la razón para entrar a diario.

Un solo endpoint read-only que agrega lo que el agente hizo en un rango: citas (por estado,
reconfirmadas, no-shows, por origen), conversaciones (atendidas por el agente vs escaladas a
humano), pedidos y cotizaciones del canal, encuestas (satisfacción) y pesos recuperados por
cobranza. Cada bloque va gateado por la capacidad de su pack (sin el pack, el bloque no aparece).

RBAC: admin (es el tablero del dueño). No requiere un flag propio: requiere `canal_whatsapp`
(sin canal no hay agente que reportar). SQL read-only agregado aquí mismo (es un reporte, no toca
dominio — espejo de modules/reportes).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import Principal, require_role
from core.auth.features import get_capacidades, require_feature
from core.config.timezone import COLOMBIA_TZ, today_co
from core.db.session import get_tenant_db

router = APIRouter(
    prefix="/agente", tags=["reportes-agente"],
    dependencies=[Depends(require_feature("canal_whatsapp"))],
)


def _rango(desde: date | None, hasta: date | None) -> tuple[datetime, datetime]:
    """[desde 00:00, hasta+1d 00:00) en hora Colombia. Default: últimos 30 días."""
    fin = (hasta or today_co()) + timedelta(days=1)
    inicio = desde or (fin - timedelta(days=31))
    return (
        datetime.combine(inicio, time(0, 0), tzinfo=COLOMBIA_TZ),
        datetime.combine(fin, time(0, 0), tzinfo=COLOMBIA_TZ),
    )


async def _bloque_citas(s: AsyncSession, d: datetime, h: datetime) -> dict:
    filas = (
        await s.execute(
            text(
                "SELECT estado, COUNT(*) AS n, "
                "COUNT(*) FILTER (WHERE confirmacion = 'reconfirmada') AS reconfirmadas, "
                "COUNT(*) FILTER (WHERE origen = 'whatsapp') AS por_agente "
                "FROM citas WHERE inicio >= :d AND inicio < :h GROUP BY estado"
            ),
            {"d": d, "h": h},
        )
    ).all()
    por_estado = {f.estado: int(f.n) for f in filas}
    total = sum(por_estado.values())
    return {
        "total": total,
        "por_estado": por_estado,
        "agendadas_por_agente": sum(int(f.por_agente) for f in filas),
        "reconfirmadas": sum(int(f.reconfirmadas) for f in filas),
        "no_shows": por_estado.get("no_show", 0),
    }


async def _bloque_conversaciones(s: AsyncSession, d: datetime, h: datetime) -> dict:
    """Conversaciones nuevas del rango y cuántas requirieron humano (`escalada_en` en el rango)."""
    fila = (
        await s.execute(
            text(
                "SELECT COUNT(*) FILTER (WHERE creada_en >= :d AND creada_en < :h) AS nuevas, "
                "COUNT(*) FILTER (WHERE escalada_en >= :d AND escalada_en < :h) AS escaladas "
                "FROM conversaciones"
            ),
            {"d": d, "h": h},
        )
    ).first()
    nuevas, escaladas = int(fila.nuevas), int(fila.escaladas)
    return {
        "nuevas": nuevas,
        "escaladas_a_humano": escaladas,
        "pct_resueltas_sin_humano": (
            round((nuevas - escaladas) * 100 / nuevas) if nuevas else None
        ),
    }


async def _bloque_pedidos(s: AsyncSession, d: datetime, h: datetime) -> dict:
    fila = (
        await s.execute(
            text(
                "SELECT COUNT(*) FILTER (WHERE estado <> 'recibido') AS confirmados, "
                "COUNT(*) FILTER (WHERE estado = 'entregado') AS entregados, "
                "COALESCE(SUM(total) FILTER (WHERE estado = 'entregado'), 0) AS vendido "
                "FROM pedidos WHERE creado_en >= :d AND creado_en < :h"
            ),
            {"d": d, "h": h},
        )
    ).first()
    return {
        "confirmados": int(fila.confirmados),
        "entregados": int(fila.entregados),
        "vendido": str(fila.vendido),
    }


async def _bloque_cotizaciones(s: AsyncSession, d: datetime, h: datetime) -> dict:
    fila = (
        await s.execute(
            text(
                "SELECT COUNT(*) FILTER (WHERE estado <> 'abierta') AS emitidas, "
                "COUNT(*) FILTER (WHERE estado = 'aceptada') AS aceptadas, "
                "COALESCE(SUM(total) FILTER (WHERE estado = 'aceptada'), 0) AS aceptado "
                "FROM cotizaciones WHERE creado_en >= :d AND creado_en < :h"
            ),
            {"d": d, "h": h},
        )
    ).first()
    emitidas = int(fila.emitidas)
    return {
        "emitidas": emitidas,
        "aceptadas": int(fila.aceptadas),
        "conversion_pct": round(int(fila.aceptadas) * 100 / emitidas) if emitidas else None,
        "total_aceptado": str(fila.aceptado),
    }


async def _bloque_cobranza(s: AsyncSession, d: datetime, h: datetime) -> dict:
    recordatorios = (
        await s.execute(
            text(
                "SELECT COUNT(*) FROM cobranza_recordatorios "
                "WHERE enviado_en >= :d AND enviado_en < :h"
            ),
            {"d": d, "h": h},
        )
    ).scalar_one()
    recuperado = (
        await s.execute(
            text(
                "SELECT COALESCE(SUM(fm.monto), 0) FROM fiados_movimientos fm "
                "JOIN fiados f ON f.id = fm.fiado_id "
                "WHERE fm.tipo = 'abono' AND fm.creado_en >= :d AND fm.creado_en < :h "
                "AND EXISTS (SELECT 1 FROM cobranza_recordatorios r "
                "            WHERE r.cliente_id = f.cliente_id AND r.enviado_en <= fm.creado_en "
                "            AND r.enviado_en >= fm.creado_en - interval '30 days')"
            ),
            {"d": d, "h": h},
        )
    ).scalar_one()
    return {"recordatorios": int(recordatorios), "recuperado": str(recuperado)}


async def _bloque_satisfaccion(s: AsyncSession, d: datetime, h: datetime) -> dict:
    fila = (
        await s.execute(
            text(
                "SELECT COALESCE(ROUND(AVG(calificacion), 2), 0) AS promedio, COUNT(*) AS n "
                "FROM encuestas_respuestas WHERE creado_en >= :d AND creado_en < :h"
            ),
            {"d": d, "h": h},
        )
    ).first()
    return {"promedio": float(fila.promedio), "respuestas": int(fila.n)}


@router.get("/reporte")
async def reporte_agente(
    desde: date | None = Query(default=None),
    hasta: date | None = Query(default=None),
    session: AsyncSession = Depends(get_tenant_db),
    capacidades: frozenset = Depends(get_capacidades),
    _user: Principal = Depends(require_role("admin")),
) -> dict:
    """El reporte del agente en [desde, hasta] (default: últimos 30 días), por bloques de pack."""
    d, h = _rango(desde, hasta)
    reporte: dict = {
        "desde": str(desde or (h.date() - timedelta(days=31))),
        "hasta": str(hasta or today_co()),
        "conversaciones": await _bloque_conversaciones(session, d, h),
    }
    if "pack_agenda" in capacidades:
        reporte["citas"] = await _bloque_citas(session, d, h)
    if "pack_pedidos" in capacidades:
        reporte["pedidos"] = await _bloque_pedidos(session, d, h)
    if "pack_ventas" in capacidades:
        reporte["cotizaciones"] = await _bloque_cotizaciones(session, d, h)
    if "pack_cobranza" in capacidades:
        reporte["cobranza"] = await _bloque_cobranza(session, d, h)
    if "pack_postventa" in capacidades:
        reporte["satisfaccion"] = await _bloque_satisfaccion(session, d, h)
    return reporte
