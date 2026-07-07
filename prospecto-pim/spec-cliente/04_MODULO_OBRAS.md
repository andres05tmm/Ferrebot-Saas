# 04 — Projects Module (Budget vs. Actual)

## Goal
Each active project shows budgeted vs. actual spend in real time, with early alerts when
margin erodes. This is the operational heart of the system.

**Critical context:** average profit is 3-4%. A 5% overrun in any category turns the
project into a loss. Alert before that point.

## Routes
- `GET /obras` — list, filters by status/client/profitability
- `GET /obras/[id]` — project dashboard
- `GET /obras/[id]/editar` — edit metadata/dates
- `POST /api/obras/[id]/liquidar` — close/settle

## List `/obras`
Table: Name | Client | Status | Start | % Progress | Budget | Actual spend | % Current
margin | Alert (traffic light).
- Green: current margin ≥ budgeted profit
- Yellow: between 0% and budgeted profit
- Red: < 0% (loss)

## Project dashboard `/obras/[id]`
### Top KPIs (cards)
Contract value | Total spent to date | Current margin $ and % | Days worked / estimated.

### Budget vs. Actual by category
Table: Category (Materials, Labor, Equipment/Machines, Transport, Other) | Budgeted
(from item internal breakdown + proportional AIU) | Actual (expenses + purchases +
prorated payroll + machine hours) | Difference $ and % | Traffic light per category.

### Assigned machines
Table: Machine | Operator | Since | Hours worked | Billable hours | Cost to project.

### Assigned workers
Table: Worker | Employment type | Days on project this month | Prorated cost to project.

### Purchases & expenses imputed to project
Filterable: Date | Supplier | Concept | Category | Amount | Margin (if applicable).

### Daily field reports
Timeline of Telegram/manual reports: date, progress, m²/m³ done, photos.

### Actions
Assign/unassign machine | Assign/unassign worker | Register expense/purchase |
Invoice (creates Factura, triggers MATIAS API — see 15) | Settle project (final close:
computes real profit, generates closing PDF).

## Actual spend calc
`calcularGastoRealObra(obraId)` in `lib/calculos/obra.ts`:
```
actual =
  Σ Gasto.monto (by obraId)
+ Σ Compra.costoTotalCompra (by obraId)
+ Σ ProrrateoNominaObra.costoImputado (by obraId)
+ Σ (RegistroHorasMaquina * costoOperacionHora internal)  — see note
+ Σ ConsumoInventario (qty * unitCost)
```
**Machine internal cost note:** internal cost/hour of an owned machine (depreciation +
fuel + amortized maintenance) differs from the rental price billed to the client. For
v1 use a `costoOperacionHora` field on `Maquina`. `[DEFINE if he wants to track internal
cost or only billed price]`.

## Auto alerts
On each dashboard visit: margin < 50% of budgeted → yellow banner; margin < 0 → red
banner + "review category with largest deviation: X"; purchases > 15% over supplier
historical average → suggestion.

## Settlement
On settle: status → LIQUIDADA; immutable snapshot; closing PDF (contract, total spend,
final profit vs. budgeted); no new expenses (except admin).

## Acceptance criterion
Actual-spend calc matches manual sum. Traffic lights follow thresholds. Assign/unassign
works. Settled project is consistent and non-editable.
