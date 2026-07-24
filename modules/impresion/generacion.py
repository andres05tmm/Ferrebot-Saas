"""Generación automática de trabajos de impresión (ADR 0033 D2).

Un trabajo POR comanda/zona, en la MISMA transacción que la comanda (si el pedido no confirma, no
hay papel). Idempotente por `idempotency_key` UNIQUE con clave determinista `comanda:{id}:v1`:
el reintento del confirm / doble evento choca contra el UNIQUE y no duplica.

Los trabajos se generan SIEMPRE (mismo criterio que las comandas, F4: costo marginal nulo); la
superficie `/api/v1/impresion` es la que está gateada por el flag `impresion`.
"""
import json
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.events import publish


def _num(valor) -> str:
    """Decimal → texto sin ceros colgantes ni notación científica ('2.000' → '2', '10.00' → '10')."""
    return format(Decimal(valor).normalize(), "f")


async def generar_trabajos_comandas(
    session: AsyncSession, *, pedido_id: int, comanda_ids: list[int]
) -> list[int]:
    """Crea (idempotente) el trabajo de impresión de cada comanda. Devuelve los ids creados."""
    if not comanda_ids:
        return []
    filas = (
        await session.execute(
            text(
                "SELECT c.id AS comanda_id, c.zona_id, z.nombre AS zona, "
                "       p.origen, p.cliente_nombre, p.notas, "
                "       pi.nombre, ci.cantidad, pi.modificadores "
                "FROM comandas c "
                "LEFT JOIN comanda_zonas z ON z.id = c.zona_id "
                "JOIN pedidos p ON p.id = c.pedido_id "
                "JOIN comanda_items ci ON ci.comanda_id = c.id "
                "JOIN pedido_items pi ON pi.id = ci.pedido_item_id "
                "WHERE c.id = ANY(:ids) ORDER BY c.id, ci.id"
            ),
            {"ids": comanda_ids},
        )
    ).all()

    por_comanda: dict[int, dict] = {}
    for f in filas:
        trabajo = por_comanda.setdefault(f.comanda_id, {
            "tipo": "comanda", "pedido_id": pedido_id, "comanda_id": f.comanda_id,
            "zona_id": f.zona_id, "zona": f.zona or "cocina", "origen": f.origen,
            "cliente": f.cliente_nombre, "notas": f.notas, "items": [],
        })
        trabajo["items"].append({
            "nombre": f.nombre, "cantidad": _num(f.cantidad),
            "modificadores": f.modificadores or [],
        })

    creados: list[int] = []
    for comanda_id, payload in por_comanda.items():
        zona_id = payload.pop("zona_id")
        nuevo = (
            await session.execute(
                text(
                    "INSERT INTO trabajos_impresion "
                    "(tipo, payload, zona_id, pedido_id, comanda_id, idempotency_key) "
                    "VALUES ('comanda', :payload, :z, :p, :c, :key) "
                    "ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"
                ),
                {
                    "payload": json.dumps(payload, ensure_ascii=False),
                    "z": zona_id, "p": pedido_id, "c": comanda_id,
                    "key": f"comanda:{comanda_id}:v1",
                },
            )
        ).scalar_one_or_none()
        if nuevo is not None:
            creados.append(nuevo)
    if creados:
        await publish(session, "impresion_trabajo", {"trabajos": creados, "pedido_id": pedido_id})
    return creados
