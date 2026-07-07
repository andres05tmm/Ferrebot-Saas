# 05 — Inventory / Machines

## Goal
Inventory subsection. Full record per owned machine, real-time status
(available/busy/maintenance), project assignments, hours history, preventive maintenance
schedule based on hour-meter.

## Routes
- `GET /inventario` — hub with 3 tiles: Machines, Tools, Employees
- `GET /inventario/maquinas` — list
- `GET /inventario/maquinas/nueva`
- `GET /inventario/maquinas/[id]` — record
- `GET /inventario/maquinas/[id]/mantenimientos`
- `GET /inventario/maquinas/[id]/historial-horas`

## List
Table: Code | Name | Type | Status (badge) | Current project | Assigned operator |
Total hours | Next maintenance | Actions. Filters by status and type
(`[DEFINE full machine type catalog]`). Alt view: card grid with photo + color-coded
status.

## Record `/inventario/maquinas/[id]`
### General (editable)
Code, Name, Type, Plate, Serial, Year, Status, default billing price/hour, **minimum
billable hours per service** (per machine: some 3, some 5 — per machine, not global),
default operator, photo.

### Current status
If BUSY: current project, operator, since. If AVAILABLE: last project, freed date. If
MAINTENANCE: type, start, description.

### Assignment history
Table: Project | Client | Start | End | Agreed price/hour | Min hours | Total billed
hours | Revenue generated.

### Hours log
Date | project | hours worked | billable hours (min applied) | operator | source
(manual/Telegram). "+ Register today's hours" quick form.

### Maintenance
Timeline (preventive + corrective). Alert if `proximoEnHoras` near accumulated hours or
`proximoEnFecha` near. "+ Register maintenance" creates `Mantenimiento` (supplier, cost,
description, next).

### Machine KPIs
Total hours | hours this month | historical revenue (Σ hours × price) | maintenance
cost | net profitability (revenue − maintenance).

## Minimum billable hours logic
`horasFacturables = max(horasTrabajadas, asignacion.minimoHoras)` applied once per
day/service. `[DEFINE: min applies per daily service or per mobilization]`.

## Auto status
Assign → OCUPADA + operator. Free (end date) → DISPONIBLE. Maintenance start →
MANTENIMIENTO. Maintenance end → DISPONIBLE.

## Acceptance criterion
Full CRUD. Status changes reflect live. Min hours computed correctly. Maintenance alerts
fire. Net profitability correct vs. manual sum.
