"""Zona horaria Colombia (regla no negociable #4).

En disco todo es TIMESTAMPTZ en UTC; al mostrar/operar en reglas de negocio se usa hora
Colombia (America/Bogota, UTC-5, sin DST). Nunca usar date.today() crudo.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

COLOMBIA_TZ = ZoneInfo("America/Bogota")


def now_co() -> datetime:
    """Ahora en hora Colombia (aware)."""
    return datetime.now(COLOMBIA_TZ)


def to_co(dt: datetime) -> datetime:
    """Convierte un datetime (idealmente aware/UTC) a hora Colombia."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(COLOMBIA_TZ)


def today_co() -> date:
    """Fecha de hoy en Colombia (reemplaza date.today())."""
    return now_co().date()
