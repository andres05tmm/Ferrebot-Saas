"""Sync opcional con Google Calendar (write-only) — columnas de enganche del pack Agenda.

Agrega DOS columnas nullable al árbol TENANT, ambas opt-in por negocio:

- `agenda_config.google_calendar_id`: id del calendario que el negocio compartió con el service
  account de plataforma. NULL = sync apagado (el negocio usa solo dashboard/base). Si está seteado,
  el motor escribe/actualiza/borra el evento espejo en ese calendario (best-effort, nunca bloquea).
- `citas.gcal_event_id`: id del evento creado en Google para esa cita (NULL si no se sincronizó o el
  sync está apagado). Permite actualizar/borrar el espejo al reagendar/cancelar.

La base sigue siendo la fuente de verdad; Google Calendar es solo una vista que se ESCRIBE. La
credencial del service account es secreto de PLATAFORMA (env `GOOGLE_SERVICE_ACCOUNT_JSON`), no vive
por tenant. Ver `docs/agenda-google-calendar.md` (decisión SA vs OAuth + write-only).

Se aplica a TODAS las empresas vía `tools.migrate_tenants` (tenancy.md §7).

Revision ID: 0010_gcal_sync
Revises: 0009_conversaciones
Create Date: 2026-06-07
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_gcal_sync"
down_revision: str | None = "0009_conversaciones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NULL = sync apagado: el negocio comparte su calendario y pega aquí el calendar_id para activarlo.
    op.add_column("agenda_config", sa.Column("google_calendar_id", sa.Text, nullable=True))
    # Id del evento espejo en Google (NULL si no se sincronizó); para actualizar/borrar al reagendar/cancelar.
    op.add_column("citas", sa.Column("gcal_event_id", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("citas", "gcal_event_id")
    op.drop_column("agenda_config", "google_calendar_id")
