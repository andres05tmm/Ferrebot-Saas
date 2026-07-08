# 06 — Inventory / Tools

## Goal
Simple registry of minor tools (not heavy machinery): current location, status,
replacement value. Lightweight module.

## Routes
- `GET /inventario/herramientas` — list
- `GET /inventario/herramientas/nueva`
- `GET /inventario/herramientas/[id]` — record

## List
Table: Code | Name | Category | Qty | Current location | Status | Actions. Filters by
category and status.

## Record
General data + movement history (location changes manually for now — `[DEFINE if formal
transfers with sign-off are needed or direct edit is enough]`).

## Categories
`[DEFINE catalog]`: drills, hammers, shovels, wheelbarrows, hoses, extension cords, PPE,
etc.

## Acceptance criterion
Full CRUD. Location/status changes logged with date.
