# MATIAS API / Facturación electrónica — extracción profunda

> Referencia técnica del subsistema de facturación electrónica DIAN de FerreBot (`services/facturacion_service.py` 1487 líneas, `documento_soporte_service.py`, `eventos_dian_service.py`), para **reimplementarlo profesionalmente** en el SaaS multi-tenant. Es la parte más compleja y crítica del producto.
>
> Lo que aquí está es el **contrato exacto con MATIAS API v3** (payloads UBL 2.1, mapas de IDs, reglas DIAN, manejo de errores). La **arquitectura** cambia (ver §15): emisión **asíncrona (ARQ)**, secretos **por empresa**, idempotencia y máquina de estados de `facturacion-dian.md`. Conservar el *contrato con MATIAS*; cambiar el *cuándo/cómo* se invoca.

## 1. Panorama

- **Proveedor:** MATIAS API v3.0.0. Auth `https://auth-v2.matias-api.com`; base `https://api-v2.matias-api.com/api/ubl2.1`.
- **Documentos que emite:** Factura electrónica (FE), Nota crédito (NC), Nota débito (ND), Documento Soporte a no obligados (DS-NO), y eventos RADIAN sobre facturas **recibidas** de proveedores.
- **Identificadores DIAN:** `CUFE` (factura/notas), `CUDE` (documento soporte). MATIAS los devuelve como `XmlDocumentKey`.
- **En FerreBot** las credenciales son env globales; **en el SaaS** van cifradas por empresa en `secretos_empresa` y se descifran en memoria por request/job (ver `secrets.md`).

## 2. Autenticación (JWT con caché)

`_get_token()` (`facturacion_service.py:292`):

```
POST {base}/auth/login
  body: { "email": MATIAS_EMAIL, "password": MATIAS_PASSWORD, "remember_me": 0 }
  headers: Accept/Content-Type application/json
→ token = data.token | data.access_token | data.data.token | data.data.access_token
  expiry = data.expires_at (ISO) | now + data.expires_in | now + 86400
```

- Token cacheado en memoria con lock; se renueva **60 s antes** de expirar.
- En el SaaS: **caché de token por empresa** (clave = `tenant_id`), no global. Login perezoso al primer uso.
- Todas las llamadas usan `Authorization: Bearer <token>`.

## 3. Regla de oro — IDs según endpoint

MATIAS maneja **dos capas** de códigos de tipo de documento (`facturacion_service.py:78-139`):

- **POST (creación: /invoice, /notes, /ds)** → **IDs internos MATIAS** (`identity_document_id`).
- **GET (consulta: /acquirer)** → **códigos DIAN** (`identificationType`).

Confundirlos causa rechazo. Tablas a portar **verbatim**:

| Tipo | `_TIPO_ID_MATIAS` (POST) | `_TIPO_ID_DIAN` (GET) |
|---|---|---|
| CC | 1 | 13 |
| CE | 2 | 22 |
| NIT | 3 | 31 |
| RC | 6 | 11 |
| TI | 7 | 12 |
| TE | 8 | 21 |
| PA / PPN | 9 | 41 |
| DE | 10 | 42 |
| NITE | 11 | 50 |
| NUIP | 12 | 91 |
| PPT / PP | 13 | 48 |
| PEP / PE | 14 | 47 |
| SC | 15 | SC |
| CD | 20 | CD |

> ⚠️ Excepción DS (§12): en `/ds/document`, `identity_document_id="3"` produce `schemeName="31"` en el XML — que es lo que la DIAN exige para el proveedor CC de un DS (fix DSAJ25a). No reutilizar la tabla de FE para DS.

## 4. Catálogos / mapas (verificados contra la API real)

**Unidades de medida → `quantity_units_id`** (`_UNIDAD_DIAN`, `GET /quantity-units`):

| Unidad | id | | Unidad | id |
|---|---|---|---|---|
| Unidad | 70 | | Lt/litro | 821 |
| Galón/Gal | 686 | | ml (MLT) | 852 |
| Kg | 767 | | Mt/metro (Mts) | 865 |
| gramo (GRM) | 692 | | Cm (Cms) | 495 |

Default si no mapea: **70 (Unidad)**. **DS usa 1093** (no 70; 70 dispara DSFC03 en DS).

**Medios de pago → `means_payment_id`** (`_MEDIOS_PAGO`): efectivo 10 · transferencia/nequi/daviplata 42 · tarjeta/datafono 48.

**Otros IDs fijos:** `currency_id=272` (COP) · FE `type_document_id=7` · NC `5` · ND `4` · DS `11` · `operation_type_id` FE=1, DS=9 · `payment_method_id` 1=contado, 2=crédito/fiado.

**Razones de nota** (`RAZONES_NC` / `RAZONES_ND`, van en `discrepancy_response_id`):

- NC: 1 Devolución parcial · 2 Anulación de factura · 3 Rebaja/descuento · 4 Ajuste de precio · 5 Otro.
- ND: 1 Intereses · 2 Gastos por cobrar · 3 Cambio del valor · 4 Otro.

## 5. Caché de ciudades — `city_id` ≠ código DANE

`_cargar_ciudades_matias()` / `_matias_city_id(dane)` (`facturacion_service.py:150-194`):

- MATIAS usa **IDs internos de ciudad**, no el código DANE. Se carga `GET /cities` y se construye `{ dane_code:int → matias_id:str }`.
- En el payload, `customer.city_id` = el id MATIAS resuelto; default **"149" (Cartagena)**. También se manda `city_name` (fix FAK08: evita "Ciudad" vacía en el PDF).
- `clientes.municipio_dian` almacena el código DANE; si es 149 o nulo se usa el default.
- **SaaS:** la caché de ciudades es **por empresa** (cada tenant puede operar en otra ciudad/resolución). Cargar perezosamente y cachear.

## 6. Consecutivos (factura y DS)

`_siguiente_num_dian(cur)` (`:359`) y `_siguiente_num_ds()` (DS):

```sql
LOCK TABLE facturas_electronicas IN SHARE ROW EXCLUSIVE MODE;
SELECT COALESCE(
  MAX(CAST(NULLIF(regexp_replace(numero,'[^0-9]','','g'),'') AS INTEGER)),
  :NUM_DESDE - 1
) + 1
FROM facturas_electronicas;   -- piso: MATIAS_NUM_DESDE
```

- El consecutivo legal va **embebido en `numero`** (texto, p. ej. `FPR1024`); se extrae con regex.
- **No se reusa un número enviado a DIAN aunque sea rechazado** (no se filtra por estado).
- DS idéntico sobre `documentos_soporte.consecutivo`, piso `MATIAS_DS_NUM_DESDE` (poner 5 si DS1-DS4 ya existen en el portal).
- **SaaS:** pasar a **SEQUENCE por tenant y por tipo**, reservada al crear el documento en estado `pendiente` (ver `facturacion-dian.md`). En la migración, `setval` al máximo real extraído de `numero` (ver `migracion-puntorojo.md` §6).

## 7. Factura electrónica — flujo `emitir_factura(venta_id)`

(`facturacion_service.py:762`)

1. Valida env (email/password/resolución).
2. `SELECT v.* + datos fiscales del cliente` (LEFT JOIN clientes) y el detalle (LEFT JOIN productos para `tiene_iva`, `porcentaje_iva`).
3. Si la venta ya tiene `factura_estado='emitida'` → corta (no re-emite). *(En el SaaS esto lo da la `idempotency_key`.)*
4. `num_dian = _siguiente_num_dian(cur)` → `numero = MATIAS_PREFIX + num_dian`.
5. `_armar_payload(...)` (§8) y **pre-check FAU04** (§9).
6. `POST {base}/invoice` con Bearer token, timeout 30 s.
7. Éxito = `data.success == true` **y** `XmlDocumentKey`/`document_key` con **≥40 chars** (fix FAD06: success sin CUFE válido = fallo).
8. Persistir: `UPDATE ventas SET factura_numero/cufe/estado='emitida'/facturada_at` + `INSERT facturas_electronicas`. En error: `INSERT … estado='error', error_msg` (numero `ERR-{n}`).

## 8. Payload UBL de la factura — `_armar_payload`

(`facturacion_service.py:481-757`) Estructura **exacta** a replicar:

```jsonc
{
  "resolution_number": "<MATIAS_RESOLUTION>",
  "prefix": "<MATIAS_PREFIX>",
  "document_number": "<num_dian>",
  "date": "YYYY-MM-DD", "time": "HH:MM:SS",
  "type_document_id": 7,          // factura
  "operation_type_id": 1,         // siempre 1; el tipo de cliente define CF/normal
  "currency_id": 272,             // COP
  "notes": "<notas | EMPRESA_NOMBRE>",
  "graphic_representation": 1,
  "send_email": 1,                // 1 solo si hay correo real (no placeholder)
  "customer": { /* §8.1 */ },
  "tax_totals": [ /* §8.3 */ ],
  "legal_monetary_totals": { /* §8.4 */ },
  "payments": [{ "payment_method_id": 1|2, "means_payment_id": <medio>, "value_paid": <total> }],
  "lines": [ /* §8.2 */ ]
}
```

### 8.1 Cliente (`customer`) — 3 casos

- **Consumidor Final** (sin id o `222222222222`): `identity_document_id="6"`, `type_organization_id=2`, `tax_regime_id=2`, `tax_level_id=5`, `company_name="CONSUMIDOR FINAL"`, `dni="222222222222"`.
- **Empresa (NIT):** `identity_document_id="3"`, `type_organization_id=1`. Separar `dni` y `dv` (dígito de verificación, obligatorio): `"900123456-5"` → dni `900123456`, dv `5`. `regimen_fiscal`: 1=Responsable IVA (`tax_level_id=1`), 2=No responsable (`tax_level_id=5`); tolerar strings legados (`responsable_iva`/`no_responsable_iva`).
- **Persona (CC/CE/TI/PA/PPT/PEP…):** `identity_document_id = _TIPO_ID_MATIAS[tipo]` (no hardcodear "1"), `type_organization_id=2`, regimen simplificado.
- **Todos:** `country_id` (def 45), `mobile`/`email`/`address`/`company_name` **nunca vacíos** (usar fallbacks; email placeholder `sinfactura@…`), `city_id` (§5) y `city_name`.

### 8.2 Líneas (`lines`) — IVA incluido en BD

Los precios en BD **incluyen IVA**; se extrae la base gravable:

```
divisor   = 1 + pct_iva/100
base      = round(total_con_iva / divisor, 2)     # line_extension_amount
iva_val   = round(total_con_iva - base, 2)
precio_u  = round(precio_con_iva / divisor, 2)     # price_amount
```

Cada línea:
```jsonc
{
  "invoiced_quantity": <float, 4 dec>,   // soporta fracciones 0.0625=1/16
  "quantity_units_id": <int §4>,
  "line_extension_amount": <base sin IVA>,
  "free_of_charge_indicator": false,
  "description": "<NOMBRE EN MAYÚSCULAS>",
  "code": "<producto_id | SC>",
  "type_item_identifications_id": "4",
  "reference_price_id": "1",
  "price_amount": <precio_u sin IVA>,
  "base_quantity": <= invoiced_quantity>,
  "tax_totals": [{ "tax_id":"1", "tax_amount":<iva_val>, "taxable_amount":<base>, "percent":<pct> }]
}
```

> **Regla crítica (FAU04 + FAX14):** `tax_id` SIEMPRE `"1"` (IVA), incluso para exentos (entonces `percent=0`, `tax_amount=0`); **nunca** `tax_id="4"` (eso es INC → dispara FAX14). `taxable_amount` SIEMPRE = base (también en exentos), porque la DIAN exige `sum(líneas.taxable_amount) == doc.taxable_amount`.

### 8.3 `tax_totals` del documento

Calcular base/iva por línea, **redondear antes de sumar** (evita desfase FAU04):
- Si hay gravado: `{ tax_id:"1", tax_amount:total_iva, taxable_amount:subtotal_gravable, percent:19.0 }`.
- Si hay exento: `{ tax_id:"1", tax_amount:0.0, taxable_amount:subtotal_exento, percent:0.0 }`.

### 8.4 `legal_monetary_totals`

`line_extension_amount = tax_exclusive_amount = subtotal` (sin IVA); `tax_inclusive_amount = payable_amount = total_doc` (con IVA); `allowance/charge/pre_paid = 0.0`.

## 9. Validaciones DIAN conocidas (incorporar como pre-checks)

| Código | Qué significa | Defensa en el código |
|---|---|---|
| **FAU04** | `taxable_amount` de cabecera ≠ suma de líneas | `_validar_bases_antes_envio()` (`:424`): aborta si dif > 0.01. Redondear bases por línea antes de sumar |
| **FAX14** | Tributo incorrecto (INC en vez de IVA) | usar siempre `tax_id="1"` |
| **FAD06** | `success` sin CUFE válido | exigir CUFE ≥ 40 chars |
| **FAK08** | Ciudad faltante en UBL | enviar `city_id` + `city_name` |
| **DSAJ25a** | schemeName del proveedor en DS | `identity_document_id="3"` en DS |
| **DSAJ08a** | Falta `mobile` del proveedor en DS | incluir `mobile` |
| **DSFC01 / DSFC03** | DS sin `invoice_period` / unidad incorrecta | `invoice_period` + `quantity_units_id=1093` |

## 10. Respuesta de MATIAS y persistencia

- Éxito FE: `{ "success": true, "XmlDocumentKey": "<CUFE>", ... }`.
- Error: `data.message` + `data.errors` (dict) → se concatena a un `error_msg` legible (truncar 500 para BD).
- Guardar SIEMPRE traza: éxito → fila normal; error → `estado='error'` con `error_msg` (auditoría/reintento).

## 11. Endpoints GET / utilitarios

| Función | Método / endpoint | Nota |
|---|---|---|
| `obtener_pdf(cufe)` | **POST** `/documents/pdf/{cufe}` body `{regenerate:1}` | La API responde **405 a GET**; acepta POST. Devuelve PDF directo **o** JSON con `pdf.data` (base64) / `pdf.url` |
| `obtener_xml(cufe)` | GET `/documents/xml/{trackId}` | XML técnico (contabilidad/auditoría) |
| `consultar_estado_dian` | GET `/status/document/{trackId}` (con CUFE) o `/status?number=&prefix=` | validación DIAN en vivo |
| `buscar_documentos` | GET `/documents?number=&prefix=&resolution=&start_date=&end_date=&document_status=&limit=` | `document_status`: -1 todos, 0 sin validar, 1 validado |
| `consultar_consumo` | GET `/memberships/consumption` | cuota del plan MATIAS (alerta de límite) |
| `obtener_ultimo_documento` | GET `/documents/last?resolution=&prefix=` | último número emitido (reconciliar consecutivo) |
| `consultar_adquirente` | GET `/acquirer?identificationType=&identificationNumber=` | **usa códigos DIAN** (§3); valida cliente en RUT antes de emitir |
| `reenviar_correo_factura` | POST `/documents/sendmail/{trackId}` body `{email_to?}` | reenvía PDF al cliente |

## 12. Notas crédito / débito

`emitir_nota_credito` / `emitir_nota_debito` (`:1310`/`:1391`). Endpoints `POST /notes/credit` y `POST /notes/debit`.

- `type_document_id`: **5** (NC) / **4** (ND).
- Referencia obligatoria a la factura original:
  ```jsonc
  "billing_reference": { "number": "<factura_numero>", "uuid": "<factura_cufe>", "date": "<factura_fecha>" },
  "discrepancy_response": { "discrepancy_response_id": <razon_id>, "description": "<RAZONES_*[razon_id]>" }
  ```
- Líneas vía `_armar_lineas_nota()`: **asume totales SIN IVA** (el caller divide si vienen con IVA). Misma regla `tax_id="1"` + `taxable_amount` siempre.
- Persistencia: `_guardar_nota_db()` → fila en `facturas_electronicas` con `tipo` (credito/debito), `razon_id`, `factura_cufe_ref` (CUFE de la factura original).

## 13. Documento Soporte (DS-NO) — `generar_documento_soporte`

(`documento_soporte_service.py`) Para compras a **no obligados a facturar** (caso real: honorarios del desarrollador). `POST /ds/document`.

Diferencias vs factura (todas obligatorias):

- `type_document_id=11` (DS residente CC/CE; 5 = no residente), `operation_type_id=9`.
- **Sin `prefix`** (MATIAS lo toma de la resolución; enviarlo da error). Resolución propia `MATIAS_RESOLUTION_DSNO`.
- Proveedor (no obligado) va en `customer` con `identity_document_id="3"` (→ schemeName 31, fix DSAJ25a), `mobile` obligatorio (DSAJ08a), `city_id` interno MATIAS, `postal_code`.
- Línea: `quantity_units_id=1093`, `invoice_period:{ start_date: primer día del mes, description_code:1 }` (DSFC01). DS **no lleva IVA** (`percent=0`, `tax_amount=0`, `taxable_amount=valor`).
- Consecutivo propio `_siguiente_num_ds()` (§6).
- **Reintentos:** hasta 3, backoff `2**intento` s.
- **Parsing de respuesta distinto** (no hay `success`):
  - `data.errors` → rechazo de validación MATIAS (no llegó a DIAN).
  - `data.response.IsValid == "true"` → aceptado; `CUDE = XmlDocumentKey`.
  - `data.response.ErrorMessage.string[]` → rechazos ("Rechazo…") / notificaciones ("Notificación…"). Notificaciones sin rechazo = aceptado.
- Estados que persiste en `documentos_soporte.estado_dian`: `transmitido`, `rechazado_matias`, `rechazado_dian`, `error_conexion`.
- Tras CUDE válido, espera 2 s y descarga el PDF con `obtener_pdf(cude)`.

## 14. Eventos DIAN / RADIAN (facturas recibidas de proveedor)

`eventos_dian_service.py` — para **compras** (factura del proveedor llega por Gmail). Endpoints `POST /events/import-track-id` y `POST /events/send/{trackId}`.

| Evento | Código | Cuándo |
|---|---|---|
| Acuse de recibo | **030** | automático al importar la FE del proveedor (`procesar_factura_entrante`) |
| Reclamo | **031** | admin reclama (`reclamar_factura`) |
| Recibo del bien/servicio | **032** | parte de aceptar |
| Aceptación expresa | **033** | parte de aceptar (`aceptar_factura` envía 032+033) |

Se reflejan en `compras_fiscal` (`evento_030_at`…`evento_033_at`, `evento_estado` pendiente/aceptada, `evento_error`). Flujo: Gmail importa → 030 → dashboard → Aceptar (032+033) o Reclamar (031).

## 15. Reimplementación profesional en el SaaS

Conservar el contrato MATIAS (§2-§14); cambiar la arquitectura:

1. **Secretos por empresa:** `MATIAS_*`, resoluciones (FE y DS-NO), prefijo, `NUM_DESDE`, `CITY_ID`, datos del proveedor de honorarios → `secretos_empresa`/`config_empresa` cifrados; descifrar en memoria por job. Token y caché de ciudades **por `tenant_id`**.
2. **Emisión asíncrona (ARQ):** `emitir_factura` no corre en el request. El request crea el documento `pendiente` (reserva consecutivo vía SEQUENCE) y **encola** `emitir_documento(factura_id)`. Estados `fe_estado` (`pendiente→enviada→aceptada|rechazada|error→dead-letter`) de `facturacion-dian.md`. Reintentos con backoff (ya presente en DS) + dead-letter + `reconciliar_pendientes()` (usa `/status/document` y `/documents/last`).
3. **Idempotencia:** `idempotency_key` por documento; nunca emitir dos veces (reemplaza el check `factura_estado='emitida'`).
4. **Capa limpia:** un cliente MATIAS por tenant (`MatiasClient`: auth, retries, parsing) en `modules/facturacion`; los servicios de dominio arman el payload desde repositorios (sin SQL suelto). El `_armar_payload` actual mezcla query + cálculo + IO: separarlo.
5. **Feature flags:** `facturacion_electronica`, `documento_soporte`, `notas_electronicas` gobiernan endpoints/jobs (ver `feature-flags.md`).
6. **Tests de paridad:** snapshots de payload (factura gravada, exenta, mixta, CF, NIT con DV, persona CE; NC; ND; DS) que validen byte a byte contra los que hoy acepta la DIAN, más los pre-checks FAU04/FAX14/FAD06. Sandbox MATIAS con `MATIAS_AMBIENTE=pruebas` (`_TIPO_AMB=2`).

## 16. Variables de entorno MATIAS (→ secretos por empresa)

| Variable | Uso |
|---|---|
| `MATIAS_EMAIL` / `MATIAS_PASSWORD` | login (JWT) |
| `MATIAS_API_URL` | base (def `https://api-v2.matias-api.com/api/ubl2.1`) |
| `MATIAS_RESOLUTION` / `MATIAS_PREFIX` / `MATIAS_NUM_DESDE` | resolución, prefijo y piso de consecutivo de **FE** |
| `MATIAS_RESOLUTION_DSNO` / `MATIAS_DS_NUM_DESDE` | resolución y piso de consecutivo de **DS** |
| `MATIAS_CITY_ID` / `MATIAS_POSTAL_CODE` / `MATIAS_COUNTRY_ID` | ubicación por defecto (def 149 / 130001 / 45) |
| `MATIAS_AMBIENTE` | `produccion` (TipoAmb 1) \| `pruebas` (TipoAmb 2) |
| `HONORARIOS_*` / `HON_PROV_*` | datos del proveedor no obligado para el DS-NO |
| `EMPRESA_NOMBRE` | `notes` por defecto en la factura |
