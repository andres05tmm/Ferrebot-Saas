# 02 — Clients Module (CRM)

## Goal
Panel to organize all company clients before issuing quotes. Acts as a lightweight CRM
with status, history, and commercial agreements.

## Routes
- `GET /clientes` — list + search
- `GET /clientes/nuevo` — create form
- `GET /clientes/[id]` — detail
- `GET /clientes/[id]/editar` — edit form

## List `/clientes`
Table: Name | NIT | Status (colored badge) | City | Contact | # quotes | # active
projects | Actions (View / Edit). Search by name/NIT/city. Filter by status. "+ New
client" button.

## Client form
Fields (only name required, so quick capture isn't blocked):
- Name / Legal name *
- ID type (NIT / CC / CE) `[DEFINE full options with accountant]`
- NIT / ID number
- Status (dropdown)
- Contact: name, role, phone, email
- Address, City
- Commercial agreement (textarea): payment terms, negotiated discounts
- Internal notes (textarea)

## Detail `/clientes/[id]`
1. General data (editable)
2. Associated quotes — table: number, project, date, status, total. "+ New quote for
   this client" button
3. Active projects — table: name, status, progress, estimated profit
4. Issued invoices — table: number, date, total, DIAN status, payment status
5. Agreement history — dated notes timeline (optional, phase 2)

## Key logic
- No physical delete. Soft delete via `eliminadoEn`.
- Creating a quote from detail pre-fills the client.
- **MOROSO** (delinquent) status auto-suggested if unpaid overdue invoices exist (show
  alert, don't auto-change; user decides).

## Acceptance criterion
Full CRUD with validation (name required, NIT unique optional). Search/filters work.
Detail shows all relations even when empty. "New quote" from detail opens builder with
client preselected.
