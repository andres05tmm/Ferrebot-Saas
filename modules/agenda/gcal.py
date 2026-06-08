"""Cliente de Google Calendar para el sync OPCIONAL del pack Agenda (write-only en esta fase).

La base es la fuente de verdad; Google Calendar es solo una vista que se ESCRIBE: al agendar se crea
un evento espejo, al reagendar se actualiza y al cancelar se borra. NO se lee disponibilidad de Google
(eso es futuro) ni se usa OAuth: la autenticación es por SERVICE ACCOUNT de plataforma (decisión en
`docs/agenda-google-calendar.md`). El negocio comparte su calendario con el email del SA y guarda solo
su `google_calendar_id` por tenant; la credencial del SA es secreto de plataforma (env).

`CalendarPort` es el puerto que consume el motor (`AgendaService`); `GoogleCalendarClient` lo
implementa sobre `google-api-python-client`. El SDK de Google es SÍNCRONO, así que cada llamada se
corre en un hilo (`asyncio.to_thread`) para no bloquear el event loop. Las dependencias de Google se
importan PEREZOSAMENTE (igual que redis/cloudinary): el módulo carga sin el paquete instalado y los
tests usan un fake del puerto.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from core.config import get_settings
from core.config.timezone import to_co

# Permiso mínimo para escribir eventos (write-only). Leer libre/ocupado pediría otro scope: es futuro.
_SCOPES = ("https://www.googleapis.com/auth/calendar.events",)
_TZ_GOOGLE = "America/Bogota"


@dataclass(frozen=True, slots=True)
class EventoCalendario:
    """Lo que se escribe en Google para una cita. `inicio`/`fin` son aware en hora Colombia."""

    titulo: str
    descripcion: str
    inicio: datetime
    fin: datetime

    def to_body(self) -> dict[str, Any]:
        """Cuerpo del evento para la API de Google (fechas ISO + zona explícita Colombia)."""
        return {
            "summary": self.titulo,
            "description": self.descripcion,
            "start": {"dateTime": self.inicio.isoformat(), "timeZone": _TZ_GOOGLE},
            "end": {"dateTime": self.fin.isoformat(), "timeZone": _TZ_GOOGLE},
        }


@runtime_checkable
class CalendarPort(Protocol):
    """Puerto de escritura en el calendario (lo implementa `GoogleCalendarClient`; los tests lo falsean)."""

    async def crear_evento(self, calendar_id: str, evento: EventoCalendario) -> str:
        """Crea el evento y devuelve su id (para guardarlo en la cita)."""
        ...

    async def actualizar_evento(
        self, calendar_id: str, event_id: str, evento: EventoCalendario
    ) -> None:
        """Reescribe el evento existente (al reagendar)."""
        ...

    async def borrar_evento(self, calendar_id: str, event_id: str) -> None:
        """Borra el evento (al cancelar). Idempotente: si ya no existe, no es error."""
        ...


def evento_de_cita(cita: Any, servicio: Any, recurso: Any | None) -> EventoCalendario:
    """Arma el evento espejo de una cita: título = servicio + cliente; descripción = recurso + teléfono.

    Las fechas salen SIEMPRE en hora Colombia (regla no negociable #4). `recurso` puede ser None si se
    borró tras agendar: en ese caso la descripción lo omite (best-effort, no rompe el sync).
    """
    titulo = f"{servicio.nombre} — {cita.cliente_nombre}"
    lineas = []
    if recurso is not None:
        lineas.append(f"Recurso: {recurso.nombre}")
    lineas.append(f"Teléfono: {cita.cliente_telefono}")
    return EventoCalendario(
        titulo=titulo,
        descripcion="\n".join(lineas),
        inicio=to_co(cita.inicio),
        fin=to_co(cita.fin),
    )


class GoogleCalendarClient:
    """Implementación real del puerto sobre la API de Google con un service account de plataforma.

    El `Resource` de googleapiclient se construye perezoso y se cachea (la construcción hace I/O de
    discovery). Como el SDK es síncrono, cada operación se corre en un hilo. Para el piloto (1-2
    tenants, bajo volumen) un único cliente compartido alcanza; con más concurrencia habría que poolear.
    """

    def __init__(self, service_account_json: str) -> None:
        self._info = json.loads(service_account_json)
        self._service: Any | None = None

    def _build_service(self) -> Any:
        """Construye (una vez) el cliente de la API de Calendar con las credenciales del SA. PEREZOSO."""
        if self._service is None:
            from google.oauth2 import service_account  # import perezoso (dep opcional)
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_info(
                self._info, scopes=list(_SCOPES)
            )
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def _crear_sync(self, calendar_id: str, body: dict[str, Any]) -> str:
        evento = self._build_service().events().insert(calendarId=calendar_id, body=body).execute()
        return evento["id"]

    def _actualizar_sync(self, calendar_id: str, event_id: str, body: dict[str, Any]) -> None:
        self._build_service().events().update(
            calendarId=calendar_id, eventId=event_id, body=body
        ).execute()

    def _borrar_sync(self, calendar_id: str, event_id: str) -> None:
        from googleapiclient.errors import HttpError  # import perezoso (dep opcional)

        try:
            self._build_service().events().delete(
                calendarId=calendar_id, eventId=event_id
            ).execute()
        except HttpError as exc:
            # 404/410 = el evento ya no existe: borrar es idempotente, no es un fallo.
            if getattr(exc, "status_code", None) in (404, 410) or "410" in str(exc) or "404" in str(exc):
                return
            raise

    async def crear_evento(self, calendar_id: str, evento: EventoCalendario) -> str:
        return await asyncio.to_thread(self._crear_sync, calendar_id, evento.to_body())

    async def actualizar_evento(
        self, calendar_id: str, event_id: str, evento: EventoCalendario
    ) -> None:
        await asyncio.to_thread(self._actualizar_sync, calendar_id, event_id, evento.to_body())

    async def borrar_evento(self, calendar_id: str, event_id: str) -> None:
        await asyncio.to_thread(self._borrar_sync, calendar_id, event_id)


@lru_cache
def calendar_client_por_defecto() -> CalendarPort | None:
    """Cliente de plataforma desde el env (cacheado), o None si no hay service account configurado.

    None apaga el sync en toda la plataforma (sin tocar la lógica de citas). Por tenant, además, el
    sync solo actúa si `agenda_config.google_calendar_id` está seteado. Tests pueden limpiar el caché
    con `calendar_client_por_defecto.cache_clear()`.
    """
    raw = get_settings().google_service_account_json
    if not raw.strip():
        return None
    return GoogleCalendarClient(raw)
