# ADR 0012 — Documento equivalente POS electrónico como cierre fiscal de la venta de mostrador

> Estado: **Propuesto** (9 jun 2026). Insumos: `docs/audit-pos-electronico.md` (F0, verificado contra
> el código) y research de la doc oficial MATIAS v3 (9 jun 2026, docs.matias-api.com). Convierte la
> obligación legal vigente (documento equivalente POS electrónico, pequeños obligados desde
> 1-jun-2025) en la cuña de venta del SaaS: "te pongo legal sin cambiar tu forma de vender".

## Contexto (lo confirmado)

- Hoy el SaaS emite **solo FE** (`type_document_id=7`), **manual** desde el dashboard; DS/notas son
  esqueleto de datos sin código. Ni el SaaS ni el legado emitieron nunca POS electrónico (audit §1).
- **MATIAS sí lo soporta** (research 9-jun): `POST /invoice` con **`type_document_id=20`** (id
  interno, NUNCA el code DIAN), respuesta con CUDE/estado/XML — mismo endpoint y shape que FE.
  Exige el objeto **`point_of_sale`** (cashier_name, terminal_number, cashier_type, sales_code,
  address, sub_total — **todos obligatorios**) y `resolution_number` que coincida con lo registrado
  en el portal MATIAS. Notas sobre POS: mismos `/notes/credit|debit`.
- MATIAS v3 además expone: **API de autoincremento** (`POST /auto-increment/pos-documents`:
  consecutivo/prefijo los asigna MATIAS según la numeración DIAN configurada, sin colisiones;
  `PATCH /auto-increment/invoices/{uuid}` para reintentos) y **webhooks** (`document.accepted/
  rejected/voided`, firma HMAC-SHA256, 6 reintentos con backoff, secret único mostrado una vez).
- Gaps de cumplimiento que YA existen con FE manual y el POS automático multiplica (audit §4):
  no hay reconciliación (`reconciliar_pendientes()` y el webhook existen solo en
  `facturacion-dian.md`), y el histórico 5 años no se cumple (`marcar_aceptada` descarta la
  respuesta completa; `xml_url`/`pdf_url` nunca se pueblan).

## Decisión

### D1 — Se emite **POS electrónico (tipo 20)**; FE solo cuando el cliente la pide

El mostrador con consumidor final cierra con POS (su documento natural: rango propio, CF
`222222222222` ya resuelto en `armar_customer()`). **Exclusión por venta:** una venta cierra con UN
documento — si el cliente pide factura antes del cierre, se emite FE y no POS; cambio posterior =
nota (evita doble documento fiscal sobre la misma operación).

### D2 — Emisión **automática por venta**, vía el split existente

Hook post-commit de la venta: crea el `pendiente` tipo `pos` (idempotente,
`idempotency_key=f"pos:{venta_id}"` — UNIQUE ya existente) y encola `emitir_documento`. No frena la
caja (el split crear-pendiente/emitir-en-worker ya existe). El sync offline reusa el mismo hook.
*Rechazado:* lote/cierre de caja — agrega un job batch y latencia de transmisión sin ahorrar nada;
la cola ya amortigua ráfagas.

### D3 — Misma tabla y máquina de estados: `facturas_electronicas` con `tipo='pos'`

Migración tenant: `ALTER TYPE fe_tipo ADD VALUE 'pos'` (ojo: no transaccional en PG — migración
dedicada). Reusa estados, idempotencia, eventos SSE, historial y detalle. El precedente
`documentos_soporte` (tabla aparte jamás cableada) muestra el costo de divergir.

### D4 — Numeración: **API de autoincremento de MATIAS** (`/auto-increment/pos-documents`)

MATIAS autoincrementa el **consecutivo** según la resolución configurada → desaparecen las dos fuentes
de riesgo del audit (huecos por `nextval` no transaccional y colisiones concurrentes) y no se crea
`pos_consecutivo_seq`. Costo aceptado: el `pendiente` local no tiene número hasta la respuesta
(segundos en el camino feliz) — el comprobante con número DIAN se entrega/reimprime al aceptarse;
la venta como tal nunca espera. `repository.crear_pendiente` se parametriza para admitir
prefijo/consecutivo NULL hasta la aceptación (se persisten de la respuesta).
*Alternativa rechazada:* secuencia local + `/invoice` clásico (número inmediato, pero hereda huecos,
colisiones y reconciliación de rango por nuestra cuenta — exactamente lo que el audit señaló).

> **Corrección F2.4 (verificado contra el sandbox, 10-jun-2026).** Dos supuestos de este ADR resultaron
> falsos al emitir contra el sandbox real:
> 1. **El prefijo SÍ se envía** (desde `config.prefix_pos`, p.ej. `DPOS`); MATIAS NO lo deriva solo. Una
>    misma `resolution_number` puede servir a varios tipos de documento (FE, NC, ND, POS), y el endpoint
>    la desambigua por prefijo: sin prefijo responde **404** *"no se encontró una resolución activa"*. Por
>    eso `prefix_pos` es obligatorio en `pos_completa()` (fail-closed: mejor "config incompleta" que 404).
> 2. **El éxito NO trae el número en un campo estructurado.** La respuesta `200/success:true` solo lleva
>    `response.XmlFileName` (el consecutivo va en los dígitos finales, p.ej. `…00000002`) y
>    `response.StatusMessage` (`"…DPOS2, ha sido autorizada"`). El parser saca el consecutivo LIMPIO del
>    `StatusMessage` (regex `([A-Z]+)(\d+)`), con respaldo en `XmlFileName`; el **prefijo NO se parsea como
>    verdad** —la persistencia usa `config.prefix_pos`. Además se exige `software_manufacturer`
>    (owner/company/software_name) y `free_of_charge_indicator` por línea, o el endpoint da **422**.

### D5 — Config POS por tenant en `config_empresa` (claro, no secreto)

Claves nuevas: `matias_resolution_pos` (+ `matias_prefix_pos` si la resolución lo trae) y los datos
fijos del `point_of_sale` (`pos_terminal`, `pos_address`, `pos_cashier_type`); `cashier_name` =
vendedor de la venta; `sales_code` = consecutivo interno de venta; `sub_total` calculado.
`ConfigFiscal` se parametriza por tipo. Credenciales MATIAS: las mismas de FE. **Paso operativo de
onboarding** (va a `docs/onboarding-tenant.md` y al manifiesto, cruza con ADR 0011 §D3): solicitar
la resolución de numeración POS en la DIAN y registrarla en el portal MATIAS.

### D6 — Cliente: `emitir_pos()` en `MatiasClient`; la política no se toca

La doc muestra el mismo shape de respuesta que `/invoice` (CUDE como document_key) → se espera
reusar `_parsear_emision` con ajustes mínimos; verificar contra sandbox en la primera fase y, si
difiere, parser propio (el punto de variación `EmisionResultado.categoria` ya existe).
`politica.py`, backoff, `max_tries` y `_MatiasClientCache`: **sin cambios**.

### D7 — PRERREQUISITO: reconciliación por webhook + histórico propio (aplica a FE ya)

1. **Webhook**: `POST /webhooks/matias` (por tenant: cada tenant tiene cuenta MATIAS propia) —
   verifica HMAC con el secret **cifrado en `secretos_empresa`**, idempotente por `X-Webhook-ID`,
   responde <5s y procesa en el worker; suscrito a `document.accepted/rejected/voided`. Registro
   del webhook = paso de provisioning (API `POST /ubl2.1/webhooks`).
2. **Red de respaldo**: job periódico `reconciliar_pendientes()` (ya prometido en
   `facturacion-dian.md`) que barre `error`/dead-letter y consulta estado (`/documents/last` y
   búsqueda por track_id); con autoincremento, los reintentos usan `PATCH
   /auto-increment/invoices/{uuid}`.
3. **Histórico 5 años**: persistir `dian_respuesta` COMPLETA + job post-aceptada que descarga
   XML/PDF y puebla `xml_url`/`pdf_url` con storage propio.

Con emisión por venta, un dead-letter silencioso = ventas sin cierre fiscal acumulándose: esto va
**antes** del switch-on del POS automático, no después.

### D8 — Consumidor final y umbral de identificación

Default CF `222222222222` (ya implementado). El ADR fija la regla en el flujo de venta (no en el
núcleo UBL, que es puro): al superar el umbral DIAN vigente de identificación del adquirente, la
venta pide documento antes de cerrar. El valor del umbral se confirma en la fase de implementación
(normativa, puede cambiar por resolución) y vive en config de plataforma.

### D9 — Offline: diferir y alertar, nunca bloquear

Mantiene `offline-sync.md`: la venta offline se acepta siempre; el POS se encola al sincronizar
(mismo hook, idempotente). Se agrega **alerta por antigüedad** cuando un pendiente excede el plazo
reglamentario de transmisión (valor configurable; confirmar plazo vigente en implementación).

### D10 — Feature flag `pos_electronico`, dependiente de `facturacion_electronica`

Patrón `notas_electronicas` (`core/tenancy/catalogo.py`). La capa fiscal sigue transversal
(ADR 0008): una clínica puede tenerlo sin el pack `pos` del dashboard.

## Consecuencias

**A favor:** la cuña legal de venta queda operativa con cambios acotados (la máquina
política/worker/idempotencia se reusa entera); el autoincremento + webhooks de MATIAS eliminan las
dos clases de riesgo más feas (numeración y reconciliación) en vez de administrarlas; los
prerrequisitos D7 arreglan deuda de cumplimiento que ya afecta a la FE de Punto Rojo hoy.

**En contra / costo:** dependencia más profunda de MATIAS (autoincremento + webhooks — mitigada:
el histórico propio D7.3 reduce el lock-in documental); el número DIAN no es instantáneo en mostrador
(aceptado: segundos en camino feliz, y el comprobante interno sale inmediato); `ALTER TYPE ... ADD
VALUE` exige migración no transaccional cuidadosa; el webhook agrega una superficie pública nueva
(mitigada: HMAC + idempotencia + sin datos sensibles en el payload procesado).

**Pendiente operativo (Andrés, no código):** resolución de numeración POS de Punto Rojo en la DIAN
y registrarla en el portal MATIAS; confirmar en sandbox el shape de respuesta de tipo 20 y del
autoincremento antes de la fase 2.

## Fases de implementación (prompts en `docs/plan-pos-electronico-breb.md` §F2, a afinar)

1. **F2.1 — Prerrequisitos (D7):** webhook + reconciliación + histórico (aplica a FE ya; testeable
   con Punto Rojo en producción antes de tocar POS).
2. **F2.2 — POS core:** enum `pos` + flag + `ConfigFiscal` por tipo + `point_of_sale` +
   `emitir_pos()` autoincremental + hook post-venta idempotente (detrás del flag, apagado).
3. **F2.3 — Superficie:** estado fiscal de la venta en dashboard (POS/FE/pendiente/error),
   exclusión POS↔FE en el flujo "cliente pide factura", alerta de antigüedad.
4. **F2.4 — Onboarding:** sección fiscal POS en el manifiesto + paso operativo en
   `onboarding-tenant.md` + smoke en sandbox.
