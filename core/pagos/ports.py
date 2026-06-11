"""Puerto de pagos (ADR 0013): el dominio solo conoce la SOLICITUD DE COBRO; el PSP es un adaptador.

Mismo patrón que `CalendarPort` (gcal) y el cliente MATIAS: los packs piden `crear_link` /
`consultar` y el wiring decide el proveedor (Bold v1; Wompi después; `None` = modo manual).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

# Estado normalizado del cobro en el PSP (independiente del vocabulario del proveedor).
EstadoCobro = Literal["pendiente", "pagado", "vencido", "cancelado"]


@dataclass(frozen=True, slots=True)
class SolicitudCobro:
    """Lo que el dominio pide cobrar: monto + referencia idempotente + descripción + vencimiento."""

    referencia: str          # nuestra llave (≤60 chars en Bold) — idempotencia del PSP
    monto: Decimal           # COP
    descripcion: str
    vence_en: datetime | None = None


@dataclass(frozen=True, slots=True)
class LinkCobro:
    """El link creado en el PSP: su id (para consultar) + la URL que viaja al cliente."""

    proveedor_id: str
    url: str


class PagosPort(Protocol):
    """Adaptador de PSP: crear el link de cobro y consultar su estado (normalizado)."""

    async def crear_link(self, solicitud: SolicitudCobro) -> LinkCobro: ...

    async def consultar(self, proveedor_id: str) -> EstadoCobro: ...
