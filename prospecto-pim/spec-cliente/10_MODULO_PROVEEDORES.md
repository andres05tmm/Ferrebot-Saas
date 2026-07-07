# 10 — Suppliers

## Goal
Manage raw-material suppliers (asphalt plants, sand quarries), parts, fuel, services.
Purchase price history per supplier to spot variations and compare.

## Colombian market context
Main asphalt plants include IncoAsfaltos (Ecopetrol distribution channel, >30% market),
Multinsa, Asfaltos Medellín, Colombiana de Asfaltos, Asfacol. MDC-19 asphalt mix runs
~$998K–$1.06M COP/m³ material only (2026 ref). Transport billed separately by dump
truck.

## Routes
- `GET /proveedores` — list, filters by type
- `GET /proveedores/nuevo`
- `GET /proveedores/[id]` — record + history

## List
Table: Name | Type | City | Contact | # purchases last month | Total last month | Avg
last price | Actions. Filter by type.

## Record
### General
Name, NIT, type, contact (name/phone/email), address, city, notes.

### Purchase history
Filterable: Date | Concept | Category | Qty | Unit | Unit cost | Total cost | Project |
Margin (if applicable).

### Price analysis
Line chart: unit cost over time by category. Alert if latest price >15% over 6-month
avg. Side-by-side comparison with same-type suppliers.

### KPIs
Total purchased | monthly avg | top categories | last margin generated (if applicable).

## Supplier types
PLANTA_ASFALTO, CANTERA_ARENA, REPUESTOS, COMBUSTIBLE, TRANSPORTE, SERVICIOS, OTRO.

## Acceptance criterion
Full CRUD. Purchase history loaded and filterable. Price-variation alerts work.
Same-type comparisons accurate.
