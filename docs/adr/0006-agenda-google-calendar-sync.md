# ADR 0006 — Sync del pack Agenda con Google Calendar (service account, write-only, opcional)

- Estado: Aceptada
- Fecha: 2026-06-07
- Relacionado: `docs/pack-agenda-citas.md` (decisión abierta #5), pack Agenda (`modules/agenda/`)

## Contexto

El pack Agenda guarda las citas en la **base del tenant** (fuente de verdad; aislamiento por
construcción). Muchos profesionales de servicio (odontólogos, estilistas, veterinarios) ya viven en su
Google Calendar y quieren ver ahí las citas que entran por WhatsApp, sin abrir el dashboard. La
decisión abierta #5 del doc del pack dejó el sync con Google Calendar para una fase posterior; este ADR
lo resuelve para el piloto.

Restricciones del proyecto que pesan en la decisión:
- Secretos **jamás** en código ni en git (regla no negociable #5).
- Zona horaria Colombia siempre (regla #4).
- Multi-tenant DB-per-tenant: la credencial no puede ser por-usuario interactiva si queremos que un
  worker headless escriba eventos.
- El sync no puede volver frágil el flujo de citas: agendar debe funcionar aunque Google esté caído.

## Decisión

**(a) Autenticación por SERVICE ACCOUNT, no OAuth.** Una única credencial de **plataforma** (service
account de Google) escribe en los calendarios de los negocios. El negocio **comparte** su calendario
con el email del service account (permiso "hacer cambios en eventos") y nos da su `calendar_id`. Por
tenant se guarda **solo el `calendar_id`** (en `agenda_config.google_calendar_id`); la credencial del SA
es un secreto de plataforma en el entorno (`GOOGLE_SERVICE_ACCOUNT_JSON`), nunca en git ni por-tenant.

Se descarta **OAuth** en esta fase: exige un consentimiento interactivo por negocio y manejar/renovar
refresh tokens por tenant (más almacenamiento de secretos cifrados, más superficie, UX de onboarding
con pantalla de Google). El service account elimina todo eso: onboarding = "comparte tu calendario y
pega el id". El costo es que el negocio debe compartir el calendario (un paso manual), aceptable para el
piloto (1-2 tenants).

**(b) WRITE-ONLY en esta fase.** Google Calendar es una **vista que se escribe**, no una fuente:
- al **agendar** con éxito → se crea un evento espejo y se guarda su id en la cita (`citas.gcal_event_id`),
- al **reagendar** → se actualiza ese evento,
- al **cancelar** → se borra.

NO se **lee** disponibilidad (libre/ocupado) de Google: el motor calcula cupos solo con los datos del
tenant. Leer Google (para respetar eventos que el profesional creó a mano allí) es deseable pero es
**futuro**: pide otro scope, resolver conflictos de doble fuente y consistencia. El scope pedido es el
mínimo de escritura (`calendar.events`).

**(c) Best-effort: si Google falla, la cita NO falla.** Todo el sync va envuelto en `try/except` con
log; nunca propaga. Una caída de Google, un `calendar_id` mal compartido o un timeout dejan la cita
firme en la base (la verdad) sin evento espejo. El `gcal_event_id` queda NULL y se puede reconciliar
después. (Reintentos automáticos: deseable, futuro.)

**(d) Opcional y coexistente, por tenant.** `google_calendar_id` NULL = sync apagado: el negocio usa
solo dashboard/base. Un negocio puede usar dashboard, Calendar o ambos. Si no hay service account de
plataforma en el entorno, el sync está apagado para todos sin tocar la lógica de citas.

## Implementación (resumen)

- Config por tenant: `agenda_config.google_calendar_id` (nullable, migración tenant `0010_gcal_sync`).
- Cita: `citas.gcal_event_id` (nullable) guarda el id del evento espejo.
- Cliente: `modules/agenda/gcal.py` — puerto `CalendarPort` (crear/actualizar/borrar) + `GoogleCalendarClient`
  (google-auth + google-api-python-client; SDK síncrono envuelto en `asyncio.to_thread`; deps importadas
  perezosamente). Credencial desde `GOOGLE_SERVICE_ACCOUNT_JSON`.
- Enganche: `AgendaService` recibe un `CalendarPort` opcional y, tras agendar/reagendar/cancelar (tanto
  por WhatsApp como por dashboard — ambos pasan por el service), espeja el evento best-effort. El evento
  lleva título = servicio + cliente, descripción = recurso + teléfono, e inicio/fin en hora Colombia.

## Consecuencias

- (+) Onboarding trivial (compartir calendario + pegar id); sin pantallas OAuth ni refresh tokens por tenant.
- (+) Una sola credencial de plataforma cifrada en el entorno; el tenant solo expone un id no sensible.
- (+) El flujo de citas es robusto: Google es accesorio, la base es la verdad.
- (−) El negocio debe compartir su calendario con el email del SA (paso manual); si no, el sync no escribe.
- (−) Write-only: eventos que el profesional cree a mano en Google NO bloquean cupos (hasta que leamos disponibilidad).
- (−) Inserción del evento ocurre dentro de la transacción de la cita (sostiene el advisory lock durante
  la llamada de red); aceptable para el piloto, a poolear/desacoplar si crece el volumen.
- A revisitar: lectura de libre/ocupado (bidireccional), reintentos del sync, y reconciliación de citas
  con `gcal_event_id` NULL.
