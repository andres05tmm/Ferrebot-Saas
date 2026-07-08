# 08 — Payroll Module (Colombian 2026 + Patacalientes)

## Goal
Biweekly settlement of direct workers with a full Colombian payroll engine (benefits,
contributions, deductions), plus hourly settlement of patacalientes. Automatic cost
proration per project based on days actually worked on each.

## Routes
- `GET /nomina` — periods list
- `GET /nomina/nuevo-periodo`
- `GET /nomina/[periodoId]` — period settlement
- `GET /nomina/[periodoId]/trabajador/[trabajadorId]` — individual detail + proration
- `POST /api/nomina/[periodoId]/liquidar`
- `POST /api/nomina/[periodoId]/cerrar`
- `POST /api/nomina/[periodoId]/pagar`
- `POST /api/nomina/[periodoId]/transmitir-dian`

## Legal parameters
All rates/caps from `ParametrosLegales` with date validity. On period creation, freeze
the valid `ParametrosLegales` row.

**Confirmed 2026 values:**
- SMMLV: $1,750,905
- Transport allowance: $249,095 (applies up to 2 SMMLV)
- General VAT: 19%

**Contribution percentages** — `[DEFINE with PIM's accountant before production]`.
Suggested defaults are standard 2026 values but must be verified. Errors here have legal
implications.

## Direct settlement flow
### 1. Create period
Type (biweekly/monthly/weekly), start, end.

### 2. Settle
For each active DIRECTO worker:

**Earnings:**
```
diasTrabajados = days with RegistroAsistencia, no unpaid absence, in period
salarioProporcional = salarioBase * (diasTrabajados / 30)
auxilioTransporte = if eligible and salary ≤ 2 SMMLV: monthly * (dias/30)
OT day = (salarioBase/240) * 1.25 * hours  // 240 h/month convention
OT night recargo 1.75; Sundays/holidays 2.0
// [DEFINE exact multipliers per updated labor code with accountant]
totalDevengado = sum
```

**Deductions (employee):**
```
baseCotizacion = salarioProporcional + OT + Sundays  // excludes transport allowance
saludEmpleado = base * 0.04
pensionEmpleado = base * 0.04
fondoSolidaridad = if salary > 4 SMMLV: base * (0.01–0.02 by bracket) [DEFINE brackets]
otrasDeducciones = manual (garnishments, loans)
totalDeducciones = sum
```

**Net:** `totalDevengado − totalDeducciones`

**Employer contributions (not paid to worker, for real costing):**
salud, pension, ARL (by risk class), caja, SENA, ICBF = base × respective %.

**Provisions:** cesantías 8.33%, interest 12% annual, prima 8.33%, vacations 4.17%.

### 3. Proration per project (the system's differentiator)
For each settled worker:
1. Group `RegistroAsistencia` days by `obraId` (incl. null = general)
2. `costoCompletoDia = (totalDevengado + employer contributions + provisions) / diasTrabajados`
3. Per project group: `ProrrateoNominaObra { obraId, diasImputados, costoImputado }`
4. Days with no project → `obraId = null` (admin/support, not imputed to a project)

This proration feeds Projects (04) actual-spend.

## Patacaliente flow
```
totalDevengado = horasTrabajadas * tarifaHora
totalDeducciones = 0
netoPagar = totalDevengado
```
Impute 100% to one project or split by attendance. **No DIAN electronic payroll doc**
(not formal employees). `[DEFINE tax handling with accountant — possibly support doc]`.

## Individual detail screen
Cards: earnings breakdown | deductions breakdown | employer contributions (info) |
provisions (info) | net to pay (highlighted) | **proration per project** (table:
Project | days | cost imputed; total must reconcile with devengado + contributions +
provisions).

## Close & DIAN transmission
Close period (lock edits) → transmit to DIAN via MATIAS API (`/nomina-electronica`) →
each `DetalleLiquidacion` gets `cuneDian` + `fechaTransmisionDian`.

## Bank flat file
`[DEFINE if Bancolombia payroll dispersion flat file (PAB) is needed]`.

## Acceptance criterion
Period settles with no errors, matches an accountant-validated Excel reference. Project
proration sums exactly to worker total cost (no loss/duplication). Patacalientes settle
hourly with no contributions. Closed period non-editable without admin. DIAN
transmission stores CUNE.
