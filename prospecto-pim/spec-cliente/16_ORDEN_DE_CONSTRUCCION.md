# 16 — Build Order for Claude Code

The flight plan. Follow these sprints in order to avoid breaking dependencies. Each has
a verifiable deliverable.

## Rule loading (read first)
Path-scoped rules live in `rules/`. Claude Code should load only the rule matching the
file it edits:
- `rules/react.md` → `**/*.tsx`, `**/*.jsx`, `components/**`, `app/**`
- `rules/typescript.md` → TS backend (`app/api/**`, `lib/**`)
- `rules/python.md` → Python backend (only if Python stack)
One concern, one file. Don't load all specs for a change scoped to one file.

## Stack decision (do this before Sprint 0)
Default and recommended: **Next.js + TypeScript** full-stack. If the builder is faster in
Python, backend may be FastAPI (`python.md` applies, frontend still React). Pick one and
stay consistent. `01_DATA_MODEL.md` identifiers stay identical regardless.

## Sprint 0 — Bootstrap
Next.js 15 + TS + Tailwind + shadcn/ui. Prisma + PostgreSQL. Basic auth. Folder
structure. Copy specs to `docs/`, rules to project root. Base env vars. General layout
(sidebar with all sections).
**Deliverable:** runs locally, login works, menu visible.

## Sprint 1 — Full data model
Implement all of `01_DATA_MODEL.md`. Initial migration. Seed: `ConfiguracionEmpresa`
(PIM S.A.S., NIT 901462287), `ParametrosLegales` (2026 values, contribution %s marked
`[DEFINE]`), one admin `Usuario`.
**Deliverable:** all tables, seed running, Prisma Studio shows full structure.

## Sprint 2 — CRM + Quotes + Excel + PDF
1. `02_MODULE_CLIENTS.md`
2. Pure `calcularTotalesCotizacion()` in `lib/calculos/aiu.ts` + tests
3. `03_MODULE_QUOTES.md` (builder, Excel, PDF, convert-to-project stub)
**Deliverable:** create client → quote → export Excel + PDF matching real PIM quotes.

## Sprint 3 — Projects + Machines + Employees + Tools
1. `04_MODULE_PROJECTS.md` (dashboard + list, $0 actual for now)
2. `05_MODULE_INVENTORY_MACHINES.md`
3. `07_MODULE_EMPLOYEES.md`
4. `06_MODULE_INVENTORY_TOOLS.md`
5. Machine + worker assignment to projects
**Deliverable:** manage machines/employees/tools, assign to projects, project dashboard
shows assignments.

## Sprint 4 — Expenses + Suppliers + Purchases/Margins
1. `10_MODULE_SUPPLIERS.md`
2. `09_MODULE_EXPENSES_PETTY_CASH.md` (manual entry; bot later)
3. `11_MODULE_PURCHASES_MARGINS.md`
4. Update Projects (04) actual-spend to sum these
**Deliverable:** register expenses/purchases, imputed to projects, actual-spend correct.

## Sprint 5 — Colombian Payroll + Proration
1. Pure functions in `lib/calculos/nomina.ts` + exhaustive tests vs. accountant-
   validated examples
2. `08_MODULE_PAYROLL.md` (no DIAN transmission yet)
3. Proration feeding project actual-spend
**Deliverable:** biweekly period settles correctly; proration sums exactly to worker
total.

## Sprint 6 — DIAN E-Invoicing
1. `15_EINVOICING_DIAN.md`, MATIAS API in sandbox first
2. Invoice from a project, transmit payroll for a period
**Deliverable:** invoice + payroll issued in MATIAS sandbox with valid CUFE/CUNE.

## Sprint 7 — Telegram Bot
1. `14_TELEGRAM_BOT.md`
2. Claude Vision extraction of Bancolombia receipts
3. Image storage bucket
**Deliverable:** bot processes real receipts → expenses in panel. Reports + machine
hours work.

## Sprint 8 — Performance Calculator + Main Dashboard
1. `12_MODULE_PERFORMANCE_CALCULATOR.md`
2. `13_MODULE_DASHBOARD.md`, `/api/dashboard` with cache
**Deliverable:** dashboard < 2s, calculator loads projects + free simulation.

## Sprint 9 — Refinement + production
Migrate to DIAN production. Domain, HTTPS, DB backups. Onboard real PIM data. Train the
client. User docs. **Deliverable:** in production, used daily.

## Rules for Claude Code throughout
1. Read the full module spec before coding.
2. Run tests after each pure calc function — never ship a financial calc without a
   validated test case.
3. Anything not covered → leave `TODO: [DEFINE with client] ...`, don't assume.
4. Verify the acceptance criterion at the end of each sprint.
5. One commit per sprint (ideally PR per sprint).
6. Don't modify `docs/` specs without explicit approval — they are the source of truth.

## `[DEFINE]` items to validate with client/accountant before Sprints 5 & 6
- Exact social-security + parafiscal contribution %s
- Company ARL risk class (sets ARL %)
- Pension solidarity fund brackets
- OT multipliers per updated labor code
- Patacaliente tax handling (support doc or expense)
- Full machine-type catalog
- Full tool-category catalog
- Whether min billable hours applies per service or per mobilization
- Whether a Bancolombia payroll dispersion flat file is needed
- PDF library preference (react-pdf vs Puppeteer)
- Image storage provider (R2, Supabase Storage, S3)
- Internal cost/hour per owned machine (if tracking net internal profitability)
