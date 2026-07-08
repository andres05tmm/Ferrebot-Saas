# 13 — Main Dashboard

## Goal
First screen on login. Shows ALL key data at a glance (explicit client request): profit
per project, machines busy/available, cash flow, machine performance, active projects.

## Route
- `GET /` — main dashboard (auth required)

## Layout
Responsive grid: stacked on mobile, 2 columns tablet, 3 columns large desktop.

## Sections (visual priority order)
### 1. Top row: global KPIs (cards)
Month revenue (% vs. prev) | Month expenses (% vs. prev) | Estimated month profit
(traffic light) | Net cash flow (invoiced − expenses + margins).

### 2. Active projects
Compact table sorted by risk (red first): Name | Client | % progress | Budget | Spent |
Current margin | Traffic light. "View all" → `/obras`.

### 3. Profit per project (top 5)
Horizontal bars: margin $ and % of the 5 most profitable/risky projects.

### 4. Machine status
Board: total | available (green) | busy (blue, with project) | maintenance (yellow) |
damaged/retired (red). Compact list of busy machines: Machine | Project | Operator |
Hours today | Revenue today.

### 5. Machine performance (top 5 this month)
Vertical bars: machines with most billed hours this month.

### 6. Alerts
Prioritized: negative-margin projects (red) | below-threshold margin (yellow) | overdue/
upcoming maintenance | invoices unpaid > X days | quotes expiring without response |
Telegram expenses needing review | negative margins detected.

### 7. Recent activity
Compact timeline of last 20 events.

## Global filters
Header: date range (default current month), client (optional), project (optional).

## Performance
Serve all dashboard data from one aggregate endpoint `GET /api/dashboard`. Consider
5-min cache with background revalidation for heavy monthly aggregations.

## Acceptance criterion
Loads < 2s for 100+ projects and 1000+ transactions. All sections accurate vs.
individual modules. Alerts actionable (each links to relevant screen). Clean visual
hierarchy.
