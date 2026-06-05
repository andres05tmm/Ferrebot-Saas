"""Zona horaria Colombia (regla no negociable #4).

En disco todo es TIMESTAMPTZ en UTC; al mostrar/operar en reglas de negocio se usa hora
Colombia (America/Bogota, UTC-5, sin DST). Nunca usar date.today() crudo.
"""
from datetime import date, datetime, time
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


def rango_dia_co(
    desde: date | None = None, hasta: date | None = None
) -> tuple[datetime, datetime]:
    """Ventana [inicio, fin] aware en hora Colombia para filtrar columnas TIMESTAMPTZ.

    `inicio` = 00:00:00 de `desde`; `fin` = 23:59:59.999999 de `hasta`. Cualquier extremo ausente
    cae a hoy Colombia (nunca `date.today()` crudo). Postgres compara instantes, así que basta con
    pasar estos datetimes aware como parámetros.
    """
    desde = desde or today_co()
    hasta = hasta or today_co()
    inicio = datetime.combine(desde, time.min, tzinfo=COLOMBIA_TZ)
    fin = datetime.combine(hasta, time.max, tzinfo=COLOMBIA_TZ)
    return inicio, fin
