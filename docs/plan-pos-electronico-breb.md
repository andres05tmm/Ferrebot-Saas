# Plan — POS electrónico DIAN + cobro Bre-B (oferta para clientes)

> 9 jun 2026. Palanca de VENTA (no infraestructura interna): "te pongo legal con la DIAN sin cambiar
> tu forma de vender, y te doy cobro por QR gratis conciliado en caja". Contexto de mercado en
> `docs/saas-mercado-cartagena.md` (obligación vigente desde 1-jun-2025 para pequeños; Bre-B como
> rail dominante; es lo que Alegra/Siigo no diferencian). Dos frentes encadenados: el fiscal (A)
> arranca ya con un audit; el de cobro (B) requiere research de mercado ANTES de su ADR.

## Estado de base (medido contra el código)

Ya existe: `modules/facturacion/` (models, schemas, `ubl.py`, `politica.py` con `Decision`
aceptada/rechazada/error, repository), emisión vía MATIAS con job `emitir_documento` en el worker
(reintentos con backoff, dead-letter), capa fiscal **transversal** (no atada al pack `pos`, ADR 0008),
secretos MATIAS por tenant cifrados en control DB. Lo que NO sabemos con certeza: si el flujo emite el
**documento equivalente POS electrónico** (tipo de documento DIAN distinto a la factura de venta y al
documento soporte) o solo estos últimos. Por eso F0 es un audit, no código.

## Frente A — Documento equivalente POS electrónico

### F0 — Audit read-only (prompt para Claude Code)

```
Audita SOLO LECTURA el módulo fiscal de ferrebot-saas (modules/facturacion/ completo + el job
emitir_documento en apps/worker/jobs.py + lo que toque de integración MATIAS) y, como referencia de
paridad, services/facturacion_service.py del repo legado bot-ventas-ferreteria. Responde en un
informe (docs/audit-pos-electronico.md):

1. ¿Qué TIPOS de documento DIAN emitimos hoy por MATIAS? (factura electrónica de venta, documento
   soporte DS-NO, notas, ¿documento equivalente POS electrónico?). Cita archivo y función por cada
   tipo; lista los type_code/operación UBL que mandamos.
2. ¿Qué expone MATIAS API v2 (según lo extraído en docs/facturacion-matias-extract.md) para el
   documento equivalente POS electrónico: endpoint, resolución propia, numeración/prefijo aparte?
3. Gap exacto para emitir POS electrónico como CIERRE de cada venta de mostrador: modelo de datos
   (¿nueva resolución/rango por tenant en secretos?), flujo (¿emisión automática post-venta vs
   lote?), estados/reintentos (¿reusa politica.py + worker tal cual?), y qué pasa con ventas a
   consumidor final sin identificar.
4. Riesgos: numeración/consecutivos, contingencia offline (docs/offline-sync.md), histórico 5 años.

NO cambies código. El entregable es el informe con la lista de decisiones que el ADR 0012 debe tomar.
```

### F1 — ADR 0012 (lo escribe Cowork con el audit en mano)

Decisiones esperadas: cuándo se emite (cada venta vs cierre/lote — la DIAN exige transmisión, el
negocio exige no frenar la caja), modelo de resolución/rango POS por tenant (secretos cifrados, como
MATIAS FE/DS), reuso de `politica.py` + worker (mismo molde aceptada/rechazada/error + dead-letter),
feature flag (`pos_electronico`, transversal como la capa fiscal), y contingencia (cola offline →
emisión al reconectar, idempotente).

### F2 — Implementación (ADR 0012 aceptado; una sesión, rama `feat/pos-electronico`, commit por fase)

> Diseñado para "código listo hoy, switch-on mañana": F2.1 no necesita la resolución POS (aplica a
> la FE actual); F2.2 queda detrás del flag `pos_electronico` APAGADO y con config vacía. El día
> del switch-on solo se ingresan resolución/prefijo y se enciende el flag (checklist abajo).

**Prompt F2.1 + F2.2 para Claude Code:**

```
Lee docs/adr/0012-pos-electronico.md y docs/audit-pos-electronico.md. Trabaja en un git worktree
propio para la rama feat/pos-electronico (git worktree add), no en el working dir compartido.
UN commit por fase; pytest verde al cerrar cada fase; TDD.

FASE F2.1 — Prerrequisitos (D7; aplica a la FE actual, no requiere resolución POS):
1. Histórico: marcar_aceptada persiste dian_respuesta COMPLETA (no solo el cufe); job post-aceptada
   en el worker que descarga el XML (GET /documents/xml/{trackId}) y lo persiste en la BD del
   tenant (columna nueva xml_contenido, migración tenant) + puebla xml_url/pdf_url con las URLs de
   MATIAS. Idempotente; reintentos con el backoff existente.
2. Webhook: POST /webhooks/matias — por tenant (resuelve tenant por el registro del webhook, no
   confía en el payload), verifica firma HMAC-SHA256 (X-Webhook-Signature) con secret CIFRADO en
   secretos_empresa, idempotente por X-Webhook-ID (registro en BD), responde <5s y delega el
   procesamiento al worker; suscripción a document.accepted/rejected/voided actualiza estado +
   evento SSE. Ruta exenta de auth JWT pero protegida por la firma (doc: docs.matias-api.com/docs/
   endpoints/webhooks). Tool tools/registrar_webhook_matias.py <slug> <url> que llama
   POST {{url}}/ubl2.1/webhooks y guarda el secret cifrado.
3. Reconciliación: job periódico reconciliar_pendientes() en el worker — barre pendiente/error
   viejos y consulta estado en MATIAS; cierra el gap del dead-letter silencioso. Configurable
   (intervalo, antigüedad mínima).
4. Tool tools/set_config.py <slug> <clave> <valor> para escribir claves de config_empresa (molde
   set_feature; lo usa el switch-on de mañana).

FASE F2.2 — POS core (detrás del flag, APAGADO por defecto):
1. Migración tenant dedicada: ALTER TYPE fe_tipo ADD VALUE 'pos' (no transaccional — migración
   aparte, ver ADR D3).
2. Flag pos_electronico en core/tenancy/catalogo.py, dependiente de facturacion_electronica
   (patrón notas_electronicas).
3. ConfigFiscal parametrizada por tipo: claves matias_resolution_pos, matias_prefix_pos,
   pos_terminal, pos_address, pos_cashier_type en config_empresa. Si falta config POS y el flag
   está prendido → error claro al emitir, nunca payload a medias.
4. MatiasClient.emitir_pos(): POST /auto-increment/pos-documents (ADR D4: type_document_id=20 — id
   interno, NUNCA el code DIAN; objeto point_of_sale COMPLETO: cashier_name=vendedor de la venta,
   terminal_number/address/cashier_type de config, sales_code=consecutivo de venta, sub_total
   calculado). crear_pendiente admite prefijo/consecutivo NULL; se persisten de la respuesta.
   Parser: intentar _parsear_emision; si el shape difiere, parser propio (fixture del sandbox).
5. Hook post-venta: al confirmar una venta de mostrador con flag activo, crear pendiente tipo
   'pos' (idempotency_key=f"pos:{venta_id}") y encolar emitir_documento. Exclusión POS↔FE (ADR
   D1): si la venta ya tiene documento, no se crea otro; "cliente pide factura" emite FE y
   suprime el POS de esa venta.
6. Tests: idempotencia del hook (reintento de venta no duplica), exclusión, config faltante,
   webhook con firma inválida → 401, reconciliación cierra un 'error' simulado.

Reporte final: resumen por fase, decisiones fuera del plan (lista explícita), salida de pytest.
NO toques tools/manifest (la sección fiscal del manifiesto es F2.4, cruza con la rama del
onboarding). No hagas merge.
```

**F2.3 (superficie dashboard) y F2.4 (manifiesto/onboarding):** prompts cuando F2.1/F2.2 estén
mezcladas (F2.4 depende además del pack POS del ADR 0011).

### Checklist de switch-on (mañana, sin código)

1. Resolución de numeración POS de Punto Rojo en la DIAN → registrarla en el portal MATIAS.
2. `python -m tools.set_config punto-rojo matias_resolution_pos <num>` (+ prefijo si aplica, y
   pos_terminal/pos_address/pos_cashier_type).
3. `python -m tools.registrar_webhook_matias punto-rojo https://<api>/webhooks/matias`.
4. Prender el flag: `python -m tools.set_feature punto-rojo pos_electronico on` (o panel /admin).
5. Venta de prueba de mostrador → verificar CUDE en dashboard y webhook recibido; smoke en sandbox
   ANTES si MATIAS lo permite (shape del autoincremento, ADR §Pendiente operativo).

## Frente B — Cobro Bre-B (+ link de pago como complemento)

### F3 — Research (Cowork, web) → ADR 0013

Antes de decidir hay que confirmar el estado real a jun-2026 (mi corte de conocimiento no alcanza):
¿qué PSP/agregadores exponen ya cobro Bre-B con API + webhook (Wompi, Bold, bancos)? ¿Hay API directa
para comercios o solo vía entidades? ¿Costos por transacción, requisitos de registro de llave de
comercio, tiempos de acreditación? Con eso, el ADR 0013 decide:

- **PSP/agregador vs integración directa** (hipótesis: PSP — el rail directo es para entidades
  financieras; Wompi además trae link de pago Nequi/PSE/tarjeta en el mismo contrato).
- Modelo de datos: `cobros` por venta (estado pendiente→pagado→conciliado, referencia/QR,
  webhook idempotente) y **conciliación = movimiento de caja** (regla 7 de CLAUDE.md: nada toca
  caja sin movimiento; el webhook NO escribe caja directo, pasa por el service).
- Multi-tenant: credenciales del PSP por tenant cifradas (patrón MATIAS); webhook resuelve tenant
  por referencia firmada, jamás confía en el payload.
- Flag `cobro_breb` (+ `link_pago` aparte); el QR viaja también por el agente de WhatsApp ("te
  mando el QR para que pagues") — ahí se vuelve diferenciador visible.

### F4 — Implementación por fases (tras ADR 0013)

(1) modelo cobros + webhook idempotente + service de conciliación (TDD); (2) generación de QR/link
en el flujo de venta (dashboard + agente WA); (3) panel del tenant: cobros del día, no conciliados;
(4) manifiesto/onboarding: credenciales PSP como secretos del alta.

## Orden y dependencias

F0 (audit, hoy mismo) → F1 (ADR 0012) → F2. En paralelo: F3 (research+ADR 0013) puede avanzar
mientras F2 se implementa. El frente A va primero: es obligación legal (razón de compra inmediata) y
no depende de terceros nuevos; B depende de un PSP.

## Cómo se vende (para no perderlo de vista)

Paquete "ponte al día": alta con onboarding mágico (ADR 0011) + POS electrónico operando el día 1 +
QR de cobro en el mostrador y en WhatsApp. Precio ancla ≤ Alegra ($149.900 plan Pyme); el
diferenciador no es el módulo contable, es el agente que vende + cobra + factura en el mismo chat.
