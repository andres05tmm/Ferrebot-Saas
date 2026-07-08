# 07 — Inventory / Employees

## Goal
Inventory subsection showing all workers (direct + patacalientes), which machine each
operates, which project they're on today, and history. Base for Payroll (08).

## Routes
- `GET /inventario/empleados` — list
- `GET /inventario/empleados/nuevo`
- `GET /inventario/empleados/[id]` — record

## List
Table: ID | Name | Role | Type (Direct/Patacaliente) | Assigned machine | Current
project | Days this month | Actions. Filters by type, status, project.

## Record
### Personal
ID type/number, names, surnames, phone, email, address, role, hire date, retire date,
employment type.

### If DIRECTO:
Base salary, transport allowance eligible (auto-suggest if salary ≤ 2 SMMLV), EPS,
pension fund, ARL, compensation fund, bank account, bank.

### If PATACALIENTE:
Agreed hourly rate, availability notes.

### Assignments
Machine(s) operated (history), current project + change button, project history.

### Attendance log
Filterable by month: date, project, normal hours, OT day/night, Sundays/holidays,
absences. "+ Register attendance" — single day or date range (mark full week same
project).

### KPIs
Days this month | distribution by project (bar chart) | last period earnings (link to
payroll).

## "Assigned machine" logic
Direct worker may have a default machine (1-1 via `Maquina.operadorAsignado`) but can
operate others in specific assignments. Assigning an operator to a machine normally run
by someone else → alert (don't block).

## "Days this month" calc
Σ `RegistroAsistencia` in current month, grouped by absence. Incapacity/paid-leave days
count; unjustified/unpaid-leave don't count for general payroll.

## Acceptance criterion
Full CRUD distinguishing Direct vs. Patacaliente. Required fields vary by type
(conditional validation). Attendance works single + range. Days-per-project feeds
payroll proration (08).
