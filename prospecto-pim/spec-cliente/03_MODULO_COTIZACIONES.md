# 03 — Quotes Module (Builder + Excel + PDF)

## Goal
Free-form AIU quote builder + Excel export (company format) + professional PDF for the
client. A won quote converts to an active Project.

## Routes
- `GET /cotizaciones` — list, filters by status/client/date
- `GET /cotizaciones/nueva?clienteId=X` — builder
- `GET /cotizaciones/[id]` — read-only detail
- `GET /cotizaciones/[id]/editar` — edit builder
- `POST /api/cotizaciones/[id]/exportar-excel`
- `POST /api/cotizaciones/[id]/exportar-pdf`
- `POST /api/cotizaciones/[id]/convertir-obra`

## Builder
### Header
Client (searchable select, prefilled from `?clienteId=`), quote number (auto
`PIM-0XX-2026`, editable), project name *, location, issue date (default today),
validity days (default 15).

### Items table (dynamic, drag to reorder)
Columns: Description | Unit | Qty | Unit Value | Subtotal | Actions.
- Unit autocompletes from previously used values (not a closed list).
- Row subtotal computed live.
- Expandable per item: "Internal breakdown (not shown to client)" — material, labor,
  equipment. All optional.

### AIU block
Subtotal (read-only) | Administration % → value | Contingency % → value | Profit % →
value | VAT on profit % (default 19%) → value | **TOTAL CONTRACT** (highlighted).

### Terms & conditions
Textarea + quick reusable clause checkboxes (base template from real PIM quotes):
validity, scope-only, price variation by fuel/materials, weather-dependent, quantities
verified on site, out-of-scope items require new quote, timelines vary by external
factors.

### Actions
Save draft | Mark Sent/Won/Lost/Expired | Export Excel | Export PDF | Convert to Project
(only if Won).

## Shared pure function `calcularTotalesCotizacion()`
Location: `lib/calculos/aiu.ts` (TS) or `services/calculations/aiu.py` (Python).
```
subtotal = Σ(qty * unitValue)
administracion = subtotal * administracionPct
imprevistos = subtotal * imprevistosPct
utilidad = subtotal * utilidadPct
ivaUtilidad = utilidad * ivaSobreUtilidadPct
totalContrato = subtotal + administracion + imprevistos + utilidad + ivaUtilidad
```
Use Decimal. Round only at the end.

## Excel export
Match real PIM quote format (refs PIM-010-2026, PIM-011-2026): logo + company name +
NIT header; "COTIZACIÓN No. X"; client (SEÑORES) and project (OBRA); items table with
borders + gray header (ÍTEM | DESCRIPCIÓN | UND | CANT. | VR UNITARIO | VR TOTAL);
totals block right-aligned with AIU breakdown, TOTAL CONTRATO highlighted; CONDICIONES
Y OBSERVACIONES with bullets. COP format `$ #,##0`.

## PDF export
Same layout, letter/A4, logo header, page numbers. `[DEFINE library preference:
react-pdf vs Puppeteer]`.

## Convert to Project
On Won + "Convert": create `Obra` linked to quote, status PLANIFICADA, redirect to
`/obras/[newId]/editar`.

## Acceptance criterion
Full quotes saved. Excel visually equivalent to real PIM quotes. PDF professional and
downloadable. AIU math matches across screen, Excel, PDF. Won quote generates Project.
