"""Publicación de eventos vía pg_notify, transaccional (tenancy.md §6).

Se ejecuta dentro de la MISMA transacción de negocio: Postgres entrega el NOTIFY solo al
COMMIT, así el evento nunca se emite si la venta se revierte. El aislamiento lo da la base
de la empresa (cada app DB tiene su propio canal).
"""
import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CHANNEL = "ferrebot_events"


async def publish(session: AsyncSession, event: str, data: dict) -> None:
    """Encola un pg_notify en la transacción actual de `session`."""
    payload = json.dumps({"event": event, "data": data}, default=str, ensure_ascii=False)
    await session.execute(
        text("SELECT pg_notify(:chan, :payload)"),
        {"chan": CHANNEL, "payload": payload},
    )
