# 09 — Expenses & Petty Cash

## Goal
Registry of all operational expenses that aren't formal supplier purchases: lunches,
daily transport, stationery, minor parts, quick maintenance. Most enter automatically
via the Telegram bot processing Bancolombia transfer receipts (see 14).

## Routes
- `GET /gastos` — list, filters
- `GET /gastos/nuevo` — manual entry
- `GET /gastos/[id]` — detail
- `GET /gastos/revision` — inbox of bot-imported expenses needing review

## List
Table: Date | Category | Description | Amount | Project | Machine | Responsible |
Payment method | Source (Manual/Telegram) | Actions. Filters: date range, category,
project, source, needs-review. Alt view: grouped by day/week/month with totals.

## Manual entry
Date (default today), category (dropdown), description *, amount *, project (optional),
machine (optional), responsible, payment method (default Bancolombia transfer),
reference number, attach receipt.

## Review inbox `/gastos/revision`
Shows expenses with `requiereRevision = true` (bot couldn't extract confidently). Quick
approval UI: view original capture, view extracted fields, edit and approve/reject.

## Categories (extensible)
REPUESTOS, MANTENIMIENTO_MAQUINA, ALMUERZOS, TRANSPORTE_PERSONAL, COMBUSTIBLE,
PAPELERIA, SERVICIOS_PUBLICOS, ARRIENDO, IMPUESTOS, OTRO.

**Note:** owned-machine fuel is NOT tracked here — it's already included in the
rental price/hour billed to the client. Only non-standard operational spend enters here.

## Petty cash report
Total spent this month | by category (donut) | by project (bar) | top 10 expenses |
month-over-month comparison.

## Telegram bot integration
See `14_TELEGRAM_BOT.md`. Summary: user sends Bancolombia receipt → bot extracts via
Claude Vision → asks category + project + description → creates `Gasto` with
`origenRegistro = TELEGRAM_BOT`; low confidence → `requiereRevision = true`.

## Acceptance criterion
Manual CRUD works. Bot expenses appear frictionless with full data. Project-imputed
expenses reflect in project actual-spend. Review inbox allows batch approve/edit.
