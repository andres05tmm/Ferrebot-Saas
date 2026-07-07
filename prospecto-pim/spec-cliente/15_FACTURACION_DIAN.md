# 15 — DIAN E-Invoicing (MATIAS API)

## Goal
Integrate with MATIAS API (https://matias-api.com) to issue electronic invoices, credit/
debit notes, support documents, and transmit electronic payroll directly to DIAN under
Software Propio mode.

## Why MATIAS API
- REST API directly to DIAN, no intermediary technology provider
- Software Propio mode (Resolution 000042 of 2020) — PIM S.A.S. authorized directly
- Node.js/TypeScript compatible (also Python via HTTP)
- Competitive pricing (PYME plans from ~$220K COP/year for 5,000 docs)
- Electronic payroll included in all plans
- Digital certificate: ~$104K COP/year per NIT

## Prerequisite (operational, not code)
1. Register MATIAS account
2. DIAN authorization of PIM S.A.S. as Software Propio (MATIAS assists)
3. Acquire digital certificate (biometric activation)
4. Configure DIAN invoicing resolution (authorized numbering range)
5. Env vars: `MATIAS_API_KEY`, `MATIAS_BASE_URL` (prod vs sandbox),
   `MATIAS_NIT_EMISOR`, `MATIAS_RESOLUCION_DIAN`

## Documents issued
### 1. Sales invoice
From `/obras/[id]` ("Invoice" button) or `/facturacion/nueva`:
1. Select project/client + items → create `Factura` (BORRADOR)
2. On issue: call MATIAS sales-invoice endpoint
3. Receive CUFE + XML + signed PDF
4. Status → ACEPTADA_DIAN or RECHAZADA_DIAN
5. Store `xmlUrl`, `pdfUrl`, `cufeDian`, full `respuestaDianJson`
6. Optionally email client automatically

### 2. Credit note
To void/correct an accepted invoice. References original via CUFE.

### 3. Debit note
For later increases (add-ons, upward adjustments).

### 4. Support document
For purchases from persons not required to e-invoice (patacalientes, small suppliers).
`[DEFINE with accountant when to use]`.

### 5. Electronic payroll
On closing a `PeriodoNomina` + transmitting: per `DetalleLiquidacion` (directs only),
call MATIAS payroll endpoint → store `cuneDian` + `fechaTransmisionDian`.

## HTTP wrapper
Module `lib/matias/` (TS) or `services/matias/` (Python):
- `emitirFacturaVenta(data)` → { cufe, xmlUrl, pdfUrl, raw }
- `emitirNotaCredito(...)`, `emitirNotaDebito(...)`, `emitirDocumentoSoporte(...)`
- `transmitirNomina(detalle)` → { cune, raw }
- `consultarEstadoDocumento(cufe)`
Errors caught, stored as RECHAZADA_DIAN with full response for diagnosis.

## Resilience
Network failure → retry up to 3× exponential backoff. DIAN rejection → no auto-retry,
show error with detail. Manual retry queue for RECHAZADA docs with edit-and-resend.

## Sandbox vs. production
Dev points to MATIAS sandbox (docs don't count against plan). Switch to prod only after
validation.

## Compliance
XML storage ≥ 5 years (DIAN) — `xmlUrl` to immutable storage. Never modify issued
invoices (corrections via credit/debit note). Audit log of all DIAN transactions.

## References
- https://matias-api.com/preguntas-frecuentes/
- https://matias-api.com/nomina-electronica/

## Acceptance criterion
Issue a sales invoice in sandbox, get valid CUFE. XMLs/PDFs stored and downloadable.
Payroll transmission for a closed period works. DIAN errors shown clearly. Retries
handle network failures.
