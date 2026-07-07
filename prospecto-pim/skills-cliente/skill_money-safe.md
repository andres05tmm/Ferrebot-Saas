---
name: money-safe
description: Enforces safe money handling in this ERP. Triggers whenever code touches currency, prices, AIU/VAT, payroll, margins, or any COP amount. Prevents float rounding bugs and duplicated calculation logic.
---

# Money-Safe

Financial correctness is non-negotiable in this project. Margins are 3-4%; a rounding
bug can flip profit to loss.

## Rules
- **Never `float`/JS `number` for money.** Use `Decimal` (decimal.js / Prisma.Decimal /
  Python `Decimal`). DB columns `@db.Decimal(18,4)`.
- **All money math in pure functions** under `lib/calculos/` (TS) or
  `services/calculations/` (Python). Never inline in UI, routes, Excel, or PDF code.
- **One source of truth per formula.** AIU, VAT, payroll, margin each computed in exactly
  one function. UI/Excel/PDF/bot all call it. No re-implementation.
- **Round only at the end**, never on intermediate steps.
- **Every calc function has a unit test** validated against a manual/accountant example
  before it's considered done.

## AIU formula (canonical)
```
subtotal = Σ(qty * unitValue)
administracion = subtotal * administracionPct
imprevistos = subtotal * imprevistosPct
utilidad = subtotal * utilidadPct
ivaUtilidad = utilidad * ivaSobreUtilidadPct   // VAT on profit ONLY
total = subtotal + administracion + imprevistos + utilidad + ivaUtilidad
```

## Colombian legal params
Never hardcode SMMLV, transport allowance, or contribution %s. Read from
`ParametrosLegales` (date-based validity). 2026: SMMLV 1,750,905; transport 249,095.

## Formatting
`formatCOP()` shared helper only. Never format inline. `$ #,##0` with thousands
separator.

## Red flags — stop and fix
- `parseFloat` / `Number()` on a currency value used for math
- A percentage literal in JSX or a route handler
- Two functions computing the same total
- A calc function with no test
