# Fase 6 (facturación electrónica DIAN vía MATIAS) — notas de entregables

> Contrato MATIAS en `docs/facturacion-matias-extract.md`. Pipeline **síncrono** de emisión completo
> y probado de punta a punta (E1–E4 + RC). La resolución asíncrona del estado DIAN (reconciliador /
> webhook) y la validación de formato contra el sandbox quedan como deuda explícita. No borrar hasta
> cerrar esos follow-ups.

## Entregables (HECHO)
- **E1 — núcleo UBL puro** (`modules/facturacion/{schemas,ubl}.py`): mapas de IDs verbatim (§3/§4),
  math de línea con IVA incluido (§8.2), `tax_totals`/`legal_monetary_totals` (§8.3/§8.4), 3 casos de
  cliente (§8.1), pre-check FAU04 (§9). Decimal puro, tenant-neutral (ciudad/correo por input).
- **E2 — `MatiasClient` por empresa** (`matias_client.py`): httpx perezoso/memoizado, auth JWT con
  caché por instancia, emisión, caché de ciudades; parsers puros (token / emisión FAD06 / ciudades).
- **E3 — persistencia + servicio** (`models.py`, `repository.py`, `service.py`): `FacturaElectronica`,
  `SqlFacturacionRepository` (consecutivo por SEQUENCE, estados con `pg_notify`, `datos_para_factura`),
  `FacturacionService` (`crear_pendiente` idempotente + `emitir`).
- **E4a — categoría + política** (`politica.py`): `_parsear_emision` fija `categoria`;
  `decidir_emision` (aceptada/rechazada terminales; error reintenta hasta `MAX_INTENTOS` → dead-letter).
- **E4b-1 — `emitir` dirigido por la política**: devuelve `Decision`; persiste `aceptada|rechazada|error`.
- **E4b-2 — worker ARQ** (`apps/worker/`): `emitir_documento` (Decision → Retry/dead_letter/terminal),
  backoff exponencial acotado; `cargar_config_matias` (movido a `modules/facturacion/config.py`).
- **E4e — endpoint + gate** (`router.py`, `core/auth/features.py`): `POST /api/v1/facturas` thin
  (crea pendiente + encola) con `require_feature("facturacion_electronica")` (404 genérico).
- **RC-1 — cableado de runtime**: `get_capacidades`/`get_facturacion_service`/`get_enqueuer` reales,
  pool ARQ en el lifespan del API, `on_startup` del worker (`crear_servicio` por empresa).
- **RC-2 — smoke E2E** (`tests/test_e2e_facturacion.py`): `pendiente → aceptada` extremo a extremo
  (API real + worker, MATIAS mockeado con `httpx.MockTransport`).

## Bugs latentes que destapó el smoke E2E (corregidos en RC-2)
- **`core/db/session.py` — `get_tenant_db(request)` sin anotación:** sin `request: Request` FastAPI lo
  trataba como **query param obligatorio** en TODOS los endpoints que dependen de él (ventas,
  inventario, caja, fiados, facturas). No se detectó porque ningún test golpeaba esos endpoints por
  HTTP (las pruebas llaman al servicio directo). Fix: anotar `request: Request`.
- **`matias_client._a_json` — payload con `Decimal`:** `emitir_factura` hacía `json=payload`, pero el
  payload de E1 lleva montos `Decimal` (no serializables) → se tragaba como "fallo de transporte". Los
  unit de E2 usaban un payload sin Decimales. Fix: `content=_a_json(payload)` con `default=float`.

## Diferido / follow-up (deuda explícita — NO perder)
- **E4c (reconciliador) + E4d (webhook) — resolución ASÍNCRONA del estado DIAN:** hoy el pipeline es
  síncrono (`pendiente → aceptada|error` según la respuesta inmediata de `/invoice`). La aceptación
  REAL de la DIAN puede tardar; falta el job que consulta `/status/document` (con CUFE) y reconcilia
  `enviada → aceptada|rechazada`, más el webhook de MATIAS si lo ofrece. **PENDIENTE de confirmar el
  contrato de `/status` y `/documents` contra el sandbox MATIAS** (`MATIAS_AMBIENTE=pruebas`).
- **Formato de montos en el payload (number vs string):** `_a_json` emite los `Decimal` como JSON
  number (`default=float`), espejo del original (`round(x,2)` → number). **Confirmar contra el sandbox**
  el formato exacto (number vs string, nº de decimales, redondeo) — encaja en E4d.
- **Smokes HTTP para los routers del API (ventas/caja/inventario/fiados):** la AUSENCIA de pruebas que
  golpeen esos endpoints por HTTP ocultó el bug de `get_tenant_db`. Agregar smokes HTTP (con
  `dependency_overrides` + `ASGITransport`, patrón de `test_facturacion_router`/`test_e2e_facturacion`)
  como guardarraíl para los routers existentes.
- **Caché de `MatiasClient` por tenant:** `on_startup` arma un `MatiasClient` nuevo por emisión
  (`apps/worker/main._ServicioEmision`); cachear por `tenant_id` (reusar token/ciudades) es optimización.
