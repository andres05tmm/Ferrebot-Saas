# ADR 0020 — Recepción de facturas de proveedor por QR/CUFE (MATIAS RADIAN)

- **Estado:** propuesto (17 jun 2026)
- **Contexto:** `modules/compras_fiscal/` (RADIAN-FE Slice 6b ya existe: `radian_service.py`, columnas de
  evento), `modules/facturacion/matias_client.py` (`importar_track_id` / `enviar_evento`),
  `modules/proveedores/` (`facturas_proveedores`, cuenta por pagar con `fecha_vencimiento` ya
  capturable — ADR 0019 + follow-up), `modules/compras/` (compra que mueve inventario),
  `docs/facturacion-matias-extract.md §14`, `docs/facturacion-dian.md`, `docs/feature-flags.md`.

## Contexto

Las ferreterías **reciben** facturas electrónicas de sus proveedores. Hoy registrarlas es manual:
foto a Cloudinary como soporte y digitación del proveedor, total y (desde el follow-up de ADR 0019) el
vencimiento. Es lento y propenso a error, justo cuando ese dato alimenta `pack_pagar` (avisos de
cuentas por pagar).

Toda factura electrónica colombiana trae un **QR impreso** cuyo contenido lleva el **CUFE** (o una URL
DIAN que lo contiene). Con el CUFE, MATIAS puede traer el **documento oficial** (RADIAN) y, además,
permite **acusar/aceptar** el recibo ante la DIAN. La idea: escanear el QR → CUFE → traer el documento
→ registrar la cuenta por pagar con datos exactos (incl. `fecha_vencimiento` real) y, opcionalmente,
acusar recibo. El OCR de la foto queda de respaldo para papel sin QR.

### Lo que YA existe (anclaje — esto NO se reinventa)

- **Cliente MATIAS RADIAN:** `MatiasClient.importar_track_id(cufe)` (`POST /events/import-track-id`) y
  `enviar_evento(cufe, code, notes)` (`POST /events/send/{cufe}`), con `EventoResultado(ok, error_msg)`.
- **Motor de eventos:** `modules/compras_fiscal/radian_service.py` (`RadianService.importar` → acuse 030,
  `aceptar` → 032+033, `reclamar` → 031), idempotente por `evento_030_at`, fallo MATIAS persiste
  `evento_error` y responde 502. **Hoy el CUFE se teclea a mano.**
- **Datos:** tabla `compras_fiscal` con `cufe_proveedor`, `evento_030_at…033_at`, `evento_estado`,
  `evento_error`, `base`/`iva`/`total`, `soporte_url` (foto), `compra_id` (FK opcional a `compras`).
- **Credenciales MATIAS por empresa**, cifradas en el control DB (`secretos_empresa`,
  `cargar_config_matias`); `ambiente` compartido con la emisión. Patrones de worker probados (backoff,
  idempotencia por `webhooks_matias_recibidos`, seam `crear_servicio`).
- **Tres flujos distintos, hoy independientes** (clave para el alcance):
  | Flujo | Tabla | Mueve inventario | Gate |
  |---|---|---|---|
  | `compras` | `compras` + `compras_detalle` | **Sí** (ENTRADA + stock + `precio_compra`) — exige `producto_id` por línea | `pos` |
  | `compras_fiscal` | `compras_fiscal` | No (desglose IVA + eventos RADIAN) | `compras_fiscal` |
  | `facturas_proveedores` | `facturas_proveedores` + `facturas_abonos` | No (deuda: `pendiente`/`pagado`) | `pos` |

**Lo genuinamente NUEVO** es: (a) **extraer el CUFE del QR** (hoy se digita) y el **canal de captura**
(foto por Telegram / upload en dashboard), y (b) **traer los DATOS del documento** para autollenar la
cuenta por pagar — `import-track-id` hoy solo registra + acusa, no lee de vuelta la cabecera ni las
líneas.

## Decisión

Construir la recepción **encima de la capa RADIAN existente**, escaneando el QR para obtener el CUFE y
trayendo el documento oficial para registrar la **cuenta por pagar** (y su soporte fiscal) con datos
exactos. **El acuse/aceptación ante la DIAN reusa `RadianService` tal cual.**

### Flujo objetivo

```
QR impreso ──decodificar──▶ CUFE ──import-track-id (MATIAS)──▶ documento oficial
   │ (foto Telegram / upload dashboard / pegar CUFE-URL)            │
   └────────────── OCR de foto (respaldo, sin QR) ─────────────┐    ▼
                                                               ┌─ cabecera: NIT, total, fecha, fecha_vencimiento
                                                               └─ líneas: códigos/descr. DEL PROVEEDOR
                                                                          │
        registrar  facturas_proveedores (deuda, con vencimiento REAL)  ◀─┤  (v1)
                   compras_fiscal (cufe_proveedor + base/iva/total + XML)◀┘
        acuse 030 / aceptar 032+033 (RadianService — YA existe)
        ── líneas → catálogo del tenant → compras (inventario) ── (F4, asistido)
```

### Problema clave — mapeo de catálogo (define el alcance de v1)

Las líneas del documento traen **los códigos y descripciones DEL PROVEEDOR**, que **no coinciden** con
el catálogo de la ferretería (`productos.id` propios, unidades, fracciones, productos nuevos). Crear
una `compra` (que mueve inventario) exige resolver `producto_id` por línea: un mapeo **por-tenant,
difuso y de alto riesgo** (un mapeo errado infla o desangra el stock — viola el espíritu de la regla
#7: nada mueve stock sin un movimiento correcto).

**Decisión:** **v1 NO crea entradas de inventario.** Registra solo:

1. la **cuenta por pagar** (`facturas_proveedores`) con el `fecha_vencimiento` **real** del documento, y
2. el **soporte fiscal** (`compras_fiscal`: `cufe_proveedor`, `base`/`iva`/`total`, el XML oficial
   archivado), enlazable luego a una `compra`.

El inventario sigue por el flujo `/compras` (manual) hasta una fase posterior **asistida** (F4). Esta
división entrega valor inmediato (deuda exacta + vencimiento + acuse DIAN + XML) sin arriesgar el stock,
y aísla el único problema verdaderamente difícil (el mapeo) en su propia fase.

### Alcance por fases

- **F1 — Traer el documento y registrar la cuenta por pagar (núcleo, sin inventario).**
  Extender `MatiasClient` para **leer** el documento recibido tras `import-track-id` (endpoint exacto a
  confirmar — ver Preguntas abiertas #1). Mapear la **cabecera** → `facturas_proveedores` (deuda con
  `fecha_vencimiento` real) + `compras_fiscal` (`cufe_proveedor`, `base`/`iva`/`total`, XML archivado).
  El CUFE entra **pegado/manual** en F1 (reusa el camino actual). Reusa `RadianService.importar` (acuse
  030). **No** toca inventario ni el catálogo.
- **F2 — Decodificar el QR + captura por bot/dashboard.**
  Decodificar el QR de la imagen → CUFE (o URL DIAN → extraer el CUFE). Canales: **foto por Telegram**
  (extender `UpdateBot` con `photo_file_id`, `parsear_update`, y `TelegramArchivos.descargar` —que ya
  existe para voz—; hoy el bot **no** acepta fotos) y **upload en dashboard** (patrón FormData de
  `TabProveedores`; agregar una librería QR cliente: no hay ninguna hoy). Elimina la digitación del CUFE.
- **F3 — Eventos RADIAN (acuse/aceptación) — ya existe, se alimenta.**
  `RadianService.importar/aceptar/reclamar` (030/031/032/033) ya implementado y gateado por
  `compras_fiscal`. Esta fase solo **conecta** la captura escaneada y expone aceptar/reclamar en la
  superficie de "facturas recibidas" (la ruta `/facturas-recibidas` existe; falta el componente).
- **F4 — Mapeo ASISTIDO a inventario.**
  Por línea: sugerir un `producto_id` (memoria NIT+código del proveedor, match difuso por nombre), el
  admin confirma o crea el producto; al confirmar, se crea una `compra` (mueve inventario) ligada a la
  `compras_fiscal`. Único punto donde el stock se mueve. Deferido por el riesgo del mapeo.

### Idempotencia (no importar/registrar dos veces el mismo CUFE)

- **Claves naturales:** `facturas_proveedores.id` = nº de factura del proveedor (PK, ya deduplica);
  `compras_fiscal.cufe_proveedor` debe pasar a **UNIQUE por tenant** (hoy nullable, no único → migración
  de F1) para que reimportar el mismo CUFE devuelva el registro existente (200), no uno nuevo.
- `RadianService.importar` ya es idempotente por `evento_030_at` (no re-acusa).
- Si MATIAS empuja la recepción por webhook, reusar el patrón `webhooks_matias_recibidos`
  (`webhook_id` UNIQUE) — ver Preguntas abiertas #4.

### Aislamiento por tenant

Credenciales MATIAS **por empresa** (`cargar_config_matias` sobre `secretos_empresa` cifrado); cada
recepción usa las credenciales y la base de SU tenant. El CUFE, el XML y la deuda viven en la app DB del
tenant (sin `empresa_id`: la base ES la frontera). Nunca se cruzan documentos entre empresas.

### Capacidad / flag y dependencias

- **Gate: `compras_fiscal`** — donde ya vive RADIAN (`modules/compras_fiscal`). Semánticamente: "compras
  con soporte tributario".
- **Requiere credenciales MATIAS** configuradas (el mismo secreto que usa `facturacion_electronica`).
  **No** se acopla con una dependencia dura en el catálogo: si faltan las credenciales, el escaneo
  **degrada** (registra la deuda + la foto como soporte, sin importar el XML ni enviar eventos),
  igual que el patrón Cloudinary→503. Alternativa menor: declarar dependencia de `facturacion_electronica`
  si se decide que sin emisión no hay credenciales; se recomienda **degradar, no acoplar**.
- La captura **por bot** requiere `bot_telegram`; **por dashboard**, ninguna capacidad extra.

## Alternativas consideradas

- **OCR de la foto como primario → rechazado.** El QR/CUFE da el documento **oficial** (datos exactos +
  CUFE verificable + valor fiscal); el OCR es aproximado y sin respaldo DIAN. El OCR queda como
  **respaldo** para facturas en papel sin QR.
- **Digitación manual → se mantiene como fallback** (es lo de hoy); este feature la reduce, no la elimina.
- **Crear `compras` (mover inventario) en v1 → rechazado.** Exige el mapeo de catálogo (problema clave);
  se difiere a F4 asistido para no arriesgar el stock con un mapeo automático.
- **Buzón de recepción automático (poll/webhook de `document-receptions`) como primario → atractivo pero
  NO documentado en MATIAS.** Por confirmar (Preguntas abiertas #4/#6); si existe, a futuro se prefiere a
  la captura manual del QR.
- **Tabla/flag nuevos para la recepción → rechazado.** Reusa `compras_fiscal` y su flag; no hace falta un
  plano nuevo.

## Consecuencias

- El feature es sobre todo **captura + traer datos**, no plumbing nuevo: reusa `compras_fiscal`,
  `RadianService`, `MatiasClient` (import/eventos), credenciales por tenant y los patrones de worker.
- v1 entrega valor inmediato —cuenta por pagar con **vencimiento real** + acuse DIAN + XML archivado—
  **sin tocar inventario ni el catálogo**. Alimenta directo a `pack_pagar`.
- El inventario sigue por `/compras` (manual) hasta F4; no se degrada nada de lo actual.
- **Riesgo principal:** depende del contrato de MATIAS para **leer** el documento (Pregunta abierta #1).
  Si `import-track-id` no devuelve la cabecera, F1 cae a "registrar la deuda con monto/fecha tecleados +
  acuse + XML" hasta resolver el endpoint de lectura — sigue siendo mejor que hoy, pero sin autollenado.

## Preguntas abiertas — confirmar con MATIAS antes de F1

1. **(Bloqueante de F1)** ¿`import-track-id` devuelve los **datos del documento** (cabecera: NIT del
   proveedor, total, fecha, **fecha de vencimiento**) y/o las **líneas**? ¿O hay que leerlo con otro
   endpoint —`GET /document-receptions`, `GET /status/{trackId}`, o `obtener_xml` sobre el trackId
   recibido—? Sin esto no hay autollenado.
2. ¿`obtener_xml(trackId)` (hoy `GET /documents/xml/{trackId}`, para documentos **propios**) sirve para
   un trackId **recibido**? Si sí, archivamos el XML oficial del proveedor reusando el patrón D7.3.
3. Confirmar los **códigos de evento** (030 acuse, 031 reclamo, 032 recibo, 033 aceptación; aceptar =
   032+033) y que el contrato de `events/send/{cufe}` sigue vigente (ya implementado en `RadianService`).
4. ¿Existe **buzón de recepción automático** (webhook de entrada o `GET /document-receptions` para
   poll) que liste lo recibido **sin** conocer el CUFE de antemano? Si sí, habilita ingestión sin escaneo.
5. ¿El QR impreso contiene el **CUFE directo** o una **URL DIAN**
   (`https://catalogo-vpfe.dian.gov.co/document/...?documentkey=<CUFE>`)? Define cómo el decodificador
   extrae el CUFE.
6. Formato del **`import-excel`** (carga masiva de recibidas) — campos y plantilla — para la ingestión por
   lote (futuro).
