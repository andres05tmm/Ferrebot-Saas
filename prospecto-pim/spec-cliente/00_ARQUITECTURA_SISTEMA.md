# 00 — System Architecture: PIM S.A.S.

## Business context
Construcciones PIM S.A.S., a Colombian heavy-machinery rental and asphalt road
construction company. Typical contracts: from ~$30-40M COP. Quoting model is AIU
(Administration, Contingency/Imprevistos, Profit/Utilidad) + 19% VAT applied ONLY to
Profit, with percentages that vary per contract. Real profit margins: 3-4% of direct
cost, which makes early cost-overrun detection critical.

## Full system scope
1. Integrated dashboard (profit per project, machines busy/available, cash flow,
   machine performance, active projects).
2. Client panel (CRM with status, agreements, history).
3. Quotes + Excel export + PDF generation.
4. Active projects (budget vs. actual spend with alerts).
5. Inventory with 3 subsections: Machines, Tools, Employees.
6. Machine performance calculator (daily/weekly/monthly KPIs).
7. Colombian payroll 2026 (direct employees biweekly + "patacalientes" hourly).
8. Expenses and petty cash with Telegram bot integration for Bancolombia receipts.
9. Suppliers (asphalt plants, quarries, parts) with price history.
10. Purchases and transport margins ("resbalos") — margin visible per material trip.
11. Telegram bot (receipts + daily field reports).
12. DIAN electronic invoicing via MATIAS API integration.

## Tech stack
- **Frontend:** Next.js 15+ (App Router) + TypeScript + Tailwind CSS + shadcn/ui
- **Backend:** Next.js API routes + TypeScript (see typescript.md).
  ALTERNATIVE: if the builder prefers Python, backend may be FastAPI (see python.md).
  Pick ONE stack and stay consistent. Default and recommended: Next.js/TypeScript.
- **DB:** PostgreSQL (Neon or Supabase)
- **ORM:** Prisma (TS) or SQLAlchemy + Alembic (Python)
- **Excel:** `exceljs` (TS) or `openpyxl` (Python)
- **PDF:** `@react-pdf/renderer` / Puppeteer (TS) or `weasyprint` (Python)
- **Telegram bot:** `grammy` (TS) or `python-telegram-bot` (Python), webhook-based
- **Receipt OCR:** Anthropic Claude API with vision — send the Bancolombia receipt
  image and extract structured data (amount, date, recipient, reference). Do not use
  classic OCR.
- **DIAN e-invoicing:** MATIAS API (https://matias-api.com) — Software Propio mode,
  requires digital certificate (~$104K COP/year/NIT).
- **Auth:** session-based, basic multi-user (admin, accountant, supervisor, operator).
- **Hosting:** Vercel + Neon Postgres (or equivalent).

## Cross-cutting design principles
1. **No prices or percentages hardcoded.** Everything captured via forms.
2. **Every quote, project, expense, transaction is archived.** Soft delete only.
3. **Every financial calculation is a pure function** in `lib/calculos/` (TS) or
   `services/calculations/` (Python). Reused across UI, PDFs, Excel, bot.
4. **Colombian legal parameters (SMMLV, transport allowance, contribution %s) live in a
   `ParametrosLegales` table with date-based validity.** Change once a year by adding a
   row, never touching code.
5. **Anything not confirmed by the client is marked `[DEFINE]`** in each doc instead of
   assuming defaults.
6. **Each module has its own spec file with an explicit acceptance criterion** at the
   end. A module is not done until it passes.

## Document index
- `00_ARCHITECTURE.md` — this file
- `01_DATA_MODEL.md` — full schema
- `02_MODULE_CLIENTS.md` — CRM
- `03_MODULE_QUOTES.md` — builder + Excel + PDF
- `04_MODULE_PROJECTS.md` — budget vs. actual
- `05_MODULE_INVENTORY_MACHINES.md`
- `06_MODULE_INVENTORY_TOOLS.md`
- `07_MODULE_EMPLOYEES.md`
- `08_MODULE_PAYROLL.md` — Colombian 2026 + patacalientes
- `09_MODULE_EXPENSES_PETTY_CASH.md`
- `10_MODULE_SUPPLIERS.md`
- `11_MODULE_PURCHASES_MARGINS.md`
- `12_MODULE_PERFORMANCE_CALCULATOR.md`
- `13_MODULE_DASHBOARD.md`
- `14_TELEGRAM_BOT.md`
- `15_EINVOICING_DIAN.md` — MATIAS API
- `16_BUILD_ORDER.md` — dependency map and sprint plan

## Companion rule files (path-scoped, load per concern)
- `rules/react.md` → frontend files
- `rules/typescript.md` → TS backend files
- `rules/python.md` → Python backend files (only if Python stack)
Bind each rule to a path. One concern, one file. Claude Code loads only the rule
relevant to the file being edited, not all specs.
