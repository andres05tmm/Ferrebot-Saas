---
name: colombia-compliance
description: Colombian legal/tax context for this ERP. Triggers on payroll, invoicing, DIAN, taxes, benefits (prestaciones), or any regulatory logic. Ensures parameters stay configurable and legal work is flagged for accountant validation.
---

# Colombia Compliance

This ERP operates under Colombian labor and tax law. Regulatory logic must be correct
and, where unverified, flagged — not guessed.

## Configurable parameters, never hardcoded
All rates/caps/allowances live in `ParametrosLegales` with date validity. Change yearly
by adding a row. 2026 confirmed: SMMLV 1,750,905; transport allowance 249,095 (up to
2 SMMLV); VAT 19%.

## Contribution %s → flag for accountant
Social security (health/pension employee 4%/4%), employer contributions, parafiscales
(SENA 2%, ICBF 3%, Caja 4%), ARL (by risk class), solidarity fund brackets, OT
multipliers — suggested defaults exist but MUST be validated with PIM's accountant
before production. Mark any unverified value `TODO: [DEFINE with accountant]`.

## Benefits (prestaciones)
Cesantías 8.33%, interest on cesantías 12%/yr, prima 8.33%, vacations 4.17%. Transport
allowance excluded from social-security base but included for some benefit calcs — verify
each.

## DIAN e-invoicing
Via MATIAS API, Software Propio mode. Store XML ≥ 5 years. Never modify an issued
invoice — corrections via credit/debit note. Electronic payroll (CUNE) for direct
employees only; patacalientes handled separately (`[DEFINE]`).

## Patacalientes
Casual hourly workers, no formal employment, no contributions. Tax treatment
`[DEFINE with accountant]` — likely support document, not payroll.

## Rule
When implementing any regulatory calc: if the exact rate/rule isn't in
`ParametrosLegales` or confirmed in a spec, do NOT invent it. Leave a `[DEFINE]` marker
and a sensible commented default.
