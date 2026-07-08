# 11 — Purchases & Transport Margins (Resbalos)

## Goal
Detailed record of each formal supplier purchase, with special focus on material trips
(asphalt mix, sand, aggregates) where the **resbalo** (margin) is explicitly computed:
the difference between what PIM pays the supplier and what it charges the client for that
same trip. This margin is where much of the company's operational profit is generated.

## Routes
- `GET /compras` — list, filters
- `GET /compras/nueva`
- `GET /compras/[id]` — detail
- `GET /compras/resbalos` — dedicated margin report

## List
Table: Date | Supplier | Category | Concept | Qty | Unit | Unit cost | Total cost |
Client sale price | Margin $ | Margin % | Project | Actions. Filters: date, supplier,
category, project, material-trips-only.

## Purchase entry
Supplier (searchable), date *, category, project (optional), concept, **Is material
trip** checkbox (enables margin fields), qty, unit, unit purchase cost, total purchase
cost, **if material trip:** client sale price → system computes `resbalo = sale −
totalCost` live. Invoice number, attach invoice.

## Margins report `/compras/resbalos`
### Top KPIs
Total margins this month | avg margin per trip | category with highest margin %
(asphalt vs. sand).

### Detailed table
Date | Supplier | Client/Project | m³ | Purchase cost | Sale price | Margin $ | Margin %.
Sorted by margin % desc by default.

### Charts
Line: avg margin per week/month. Bar: margin by category. Bar: top suppliers by margin.

### Key alert
If margin % is negative or < 5%: warning — may mean the client was under-billed or the
supplier raised prices without adjusting the sale.

## Important distinction
Not all purchases generate margin. Parts for owned machines → cost, no margin. Asphalt
trip resold to client → margin. General fuel → no margin. External dump truck billed to
client → margin. The `esViajeMaterial` checkbox defines what enters the margins report.

## Acceptance criterion
Purchases with/without margin work. Margin computed live. Report aggregates correctly.
Low/negative margin alerts fire.
