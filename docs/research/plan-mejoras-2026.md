# Plan de mejoras 2026 — FerreBot SaaS

> ⚠️ **LEER PRIMERO `reconciliacion-con-plan-existente.md`.** Este plan se armó sin tener a la vista los ADRs 0013–0018 ni `plan-impulso-agentes-2026.md`. Gran parte de lo de aquí **ya está decidido** allá (cobranza = ADR 0015 por WhatsApp; pagos = ADR 0013 Bre-B/Bold; capa proactiva = patrón de pack; doble canal = ADR 0018). La **fuente de verdad** es `plan-impulso-agentes-2026.md` + esos ADRs. De este documento, lo que sigue siendo aporte propio: la investigación de mercado y los 3 deltas nuevos (cuentas por pagar, captura por QR, score de riesgo).
>
> Plan priorizado derivado de `benchmarking-competidores.md`. Convierte las ideas accionables en fases, alineadas con la arquitectura existente.

## Principio rector

No reinventar la dirección del proyecto, sino **acelerar el foso** que la arquitectura ya persigue: ser el *system of record + system of action* de la ferretería. Cada mejora debe (a) reutilizar la capa de herramientas/servicios existente, (b) respetar las reglas no-negociables (aislamiento, idempotencia, movimientos de stock/caja, zona horaria Colombia), y (c) hacer el dato por tenant más valioso.

## Criterios de priorización

Cada iniciativa se evalúa por **Impacto** (en retención/ticket/foso), **Esfuerzo** (S/M/L), **Reutilización** (qué tanto se apoya en lo ya construido) y **Riesgo**. Se prioriza alto impacto + alta reutilización + bajo riesgo primero.

---

## Fase 0 — Endurecimiento técnico (transversal, habilita lo demás)

Prerequisitos de seguridad y corrección para cualquier feature que toque dinero/stock. **Orden importa:** la suite de evals va primero porque es la red de seguridad de todo lo que sigue.

| Orden | # | Iniciativa | Esfuerzo | Encaje en el código |
|---|---|---|---|---|
| 1 | 17 | **Suite de evals de agente en CI** (function-call accuracy + aislamiento multi-tenant) | M | Extiende `.claude/rules/testing.md`; corpus de frases reales de mostrador. Montarla primero protege todo cambio posterior |
| 2 | 15 | **`idempotency_key` UNIQUE** en cada movimiento de venta/caja/factura | S | Refuerza la regla #8; ya está el contrato en `ai-tools.md` §4 — verificar índices UNIQUE reales. Crítico por la PWA offline (reintentos inevitables) |
| 3 | 14 | **Límites de monto/descuento en la herramienta** (no en el permiso) + umbral de confirmación por empresa | S | Extiende `config_empresa.bypass_umbral`/`bypass_confirmar` ya previstos en `ai-tools.md` §6.4; convierte el gancho en política real por empresa |
| 4 | 16a | **Validación básica de entrada** (Pydantic estricto, sanitización, montos absurdos) | S | Etapa ligera previa al despachador en `ai/` |

**Sacado de Fase 0 (agendado por disparador, no antes):**

| # | Iniciativa | Disparador |
|---|---|---|
| 16b | **Guardrail completo en instancia separada** del LLM (anti prompt-injection, cruce de tenant, no filtrar datos de otro cliente) | Lanzamiento del canal **WhatsApp** (usuario no confiable). En Telegram interno (vendedores de confianza) es baja urgencia |
| 18 | **Caché de lecturas deterministas** (precio/stock/cliente) en Redis | Cuando se **mida latencia real** y lo justifique. Es performance, no endurecimiento; meterlo antes es complejidad prematura |

**Por qué este orden:** evals (#17) primero da la red de seguridad; idempotencia (#15) y límites (#14) son corrección/seguridad esenciales; la validación básica (#16a) cubre el riesgo barato ahora, dejando el guardrail completo (#16b) para cuando el canal externo lo exija.

---

## Fase 1 — Quick wins de IA (reutilizan bot + herramientas; alto impacto, bajo costo)

Todas viven en el canal **Telegram interno** (dueño/vendedor) + capa de herramientas que ya existe → bajo riesgo, sin dependencia de WhatsApp ni consentimiento. Se distinguen dos tipos: **reactivas** (extienden el pipeline de turno) y **proactivas** (necesitan el scheduler de jobs de `nota-capa-mision-proactiva.md`). Orden reordenado por dependencia y esfuerzo:

| Orden | # | Iniciativa | Tipo | Impacto | Esfuerzo | Nuevo vs reutiliza |
|---|---|---|---|---|---|---|
| 1 | 4 | **Reportes en lenguaje natural** — "¿cuánto vendí de cemento?", "¿quién me debe más?" | Reactiva | Alto (engagement del dueño) | S | Reutiliza `generar_reporte`; mapeo intención→reporte. Primer músculo intención→herramienta, riesgo casi nulo |
| 2 | 5 | **Perfil del cliente sin fricción** (versión mínima) — frecuencia/ticket/productos/"fuerza de relación" desde las ventas | Habilitador | Medio (habilita scoring y segmentos) | M | Vista derivada sobre `ventas`/`fiados`; sin captura manual. Bloque de construcción, no feature con pantalla |
| 3 | 3 | **Cierre del día proactivo** — resumen diario por Telegram con ventas, fiados nuevos, deudas, alertas | Proactiva | Alto (hábito diario = stickiness) | M | **Monta el scheduler ARQ** barato (solo avisa al dueño, sin misiones por cliente ni dedupe) + `generar_reporte` + IA de redacción |
| 4 | 1 | **Agente de cobro de fiados** — recordatorios escalonados de cartera vencida; registra abono al llegar | Proactiva | Alto (retención + recupera caja) | M | Reusa el scheduler ya montado + `abonar_fiado`, feature `fiados`; añade misión por cliente + escalamiento |
| 5 | 22 | **Recordatorio de cuentas por pagar a proveedores** — "le debes $X a Proveedor Y, vence en N días" | Proactiva | Alto (flujo de caja del dueño) | S-M | Espejo de #1 y **más simple** (alerta interna, sin tercero externo). Reusa `facturas_proveedores`/`facturas_abonos` (ya existen) + scheduler. Único faltante: noción de **vencimiento** (columna `fecha_vencimiento` o plazo derivado por proveedor) |

**Track aparte (más pesado, frontera Fase 1/2):**

| # | Iniciativa | Impacto | Esfuerzo | Nota |
|---|---|---|---|---|
| 2 | **Captura de factura de compra** — proveedor → compra + entrada de inventario | Alto (ahorra digitación diaria) | L | Dos vías: **(preferida) escanear el QR de la factura → extraer CUFE → `import-track-id` de MATIAS → XML oficial** (datos exactos y legales, además queda radicada para acuse RADIAN); **(respaldo) OCR de la foto** con visión del LLM para facturas en papel sin QR. Nueva tool `registrar_compra_desde_documento`; Cloudinary ya está |

**Sinergia clave:** #1 (lo que te deben) + #22 (lo que debes) = **foto completa de caja** para el dueño. Alimenta features futuras de flujo de caja y el underwriting del "FerreBot Capital" (Fase 3).

**Recomendación de arranque:** **#4 (Reportes en lenguaje natural)** como primer módulo del paso 4 — menor esfuerzo, valor inmediato, riesgo casi nulo y buen primer músculo del pipeline intención→herramienta. Luego #3 monta el scheduler que habilita #1 y #22.

---

## Fase 2 — Crecimiento de ticket y retención (más producto)

**Principio de canal = principio de feature:** el canal divide las features según a quién ayudan. Lo que ayuda al **operador a manejar su negocio** va en Telegram interno; lo que **vende al cliente final** va en WhatsApp externo. Por eso el cross-sell y la recuperación de cotizaciones salen de la Fase 2 interna (ver matriz en `nota-doble-canal-telegram-whatsapp.md`).

### Fase 2 interna (Telegram, operador)

| Orden | # | Iniciativa | Impacto | Esfuerzo | Depende de |
|---|---|---|---|---|---|
| 1 | 6 | **Reposición proactiva por historial** (aviso al dueño: "Pedro va para reposición, ¿le ofrezco?") | Muy alto (ticket +40% ref. Yalo) | L | Scheduler (#3) + perfil (#5). **Es la culminación de la capa proactiva** |
| 2 | 9 | **Score de riesgo de fiado** — ¿a quién conviene fiar? | Medio (reduce mora) | M | Perfil (#5) + `fiados_movimientos`. Acompaña #1/#22 |
| 3 | 10 | **Voz en Telegram** — nota de voz → pedido | Alto (baja fricción) | M (más activación que construcción) | Ya diseñado (`ventas_voz`, `ventas_pendientes_voz`, Whisper). **Candidato a adelantar** — multiplica todas las features reactivas |

### Movido al canal WhatsApp (cliente final) — ver Fase 3 / lanzamiento WhatsApp

| # | Iniciativa | Por qué es externo |
|---|---|---|
| 7 | **Cross-sell / Shopping Assistant** ("para esa lámina, ¿tornillos?") | Solo aplica a **atención al cliente final**. El operador (Punto Rojo) ya conoce sus productos — no necesita que el bot le sugiera complementos. Exclusivo de WhatsApp |
| 8 | **Recuperación de cotizaciones no cerradas** | El concepto "cotización" vive en la superficie del cliente (`pack_ventas`, ADR 0017). Recuperar = escribirle al cliente = canal externo |

**Nota #7 (cross-sell):** cuando se implemente en WhatsApp, derivar los complementos del **historial de co-compra** (`ventas_detalle`) en vez de configuración manual; mejora solo con el uso.

---

## Fase 3 — El foso profundo (mayor esfuerzo y defensibilidad)

Avanza al ritmo de **alianzas y regulación**, no del desarrollo. **Toda la Fase 3 está en pausa por ahora** — el trabajo accionable vive en Fases 0, 1 y 2. Se documenta para retomar cuando existan los fundamentos.

### Estacionado (fuera de alcance por ahora)

| # | Iniciativa | Por qué se estaciona |
|---|---|---|
| 11 | **Pagos embebidos** — integrar una pasarela (Bold/Wompi/Mercado Pago/ePayco/PSE) para que el pago del cliente pase por FerreBot | En pausa. Revierte la decisión *"Removidos: Wompi, Bold"* → **requiere ADR** y decisión estratégica. Camino realista cuando se retome: integrar pasarela licenciada (ella es la entidad regulada), NO volverse PayFac. Cómo se haría: en `registrar_venta`, "cobrar con QR/link" → API pasarela → cliente paga → webhook idempotente → pago liquidado contra la venta → conciliación. Complementa (no reemplaza) el registro de ventas, que incluye efectivo |
| 12 | **FerreBot Capital** — adelanto de capital repagado como % de ventas | Requiere **volumen de tenants + meses de dato confiable + socio financiero (banco/fintech) + estructura regulatoria SFC**. No estamos ni cerca. Notas preservadas: es la jugada que más justifica el DB-per-tenant (el dato de ventas es el activo de underwriting); v1 sobre historial propio del tenant (no toca aislamiento), v2 comparativo entre ferreterías (requiere plano de agregados anonimizados + Habeas Data) |
| 13 | **Reposición asistida / marketplace de proveedores** — pedido automatizado al proveedor | **Muro:** los ferreteros usan proveedores **propios e independientes**. (a) No tienes sus precios → no puedes calcular el total del pedido; (b) no hay canal estándar para enviarle (WhatsApp personal, llamada, en persona); (c) cerrar el pedido es una negociación. La automatización solo funciona si el proveedor está en una plataforma (Tul/mayorista con API) — eso es construir el lado de la oferta, el mercado de dos lados que no se quiere construir. **Único pedazo barato salvable a futuro:** "lista de reposición sugerida" al dueño (alerta interna, una señal más de la capa proactiva), SIN precios ni envío ni cierre |

---

## Cumplimiento DIAN (revisar en paralelo, no es opcional)

**RADIAN — verificado en docs.matias-api.com/docs/endpoints/events-radian (jun 2026):** MATIAS **SÍ tiene API de eventos RADIAN** (`/api/ubl2.1/events`, Bearer token), orientada al **lado receptor**: importar documentos recibidos (`/import-excel` masivo o `/import-track-id` por CUFE/CUDE) → listar/consultar recepciones y estado (`/document-receptions`, `/status/{trackId}`) → `POST /send/{trackId}` envía el evento a la DIAN → reenvío de correo. Candado: no borra recepciones con eventos `ACCEPTED`/`PROCESSING`. **Cubre el flujo de acusar/aceptar facturas recibidas de proveedores** → conecta con compras, `compras_fiscal`, cuentas por pagar (#22) y OCR de compras (#2).

**Pendiente de confirmar con MATIAS antes de codificar:**
- Los **códigos de evento** (030 acuse, 032 recibo, 033 aceptación, 031 reclamo) **no se enumeran** en el doc de RADIAN. `POST /send/{trackId}` recibe solo el trackId, sin parámetro de tipo → el tipo probablemente va en las **columnas del Excel de importación** o lo secuencia MATIAS según el estado. Aclarar.
- Los **ejemplos de respuesta de RADIAN están vacíos/`...`** — la estructura del objeto evento (estados, fechas 030/032/033) no está documentada.

**Webhooks — verificado (cierra el lado emisor):** MATIAS tiene Webhooks (v3.0.0) con **26 eventos**. Los relevantes: `document.accepted` y `document.rejected` → resuelven el **estado asíncrono de la factura a crédito emitida** (lo que RADIAN no cubría) y alimentan directo tu SSE (`factura_aceptada`/`factura_rechazada`). También `document.created/emitted/voided`. Infraestructura que **encaja perfecto con FerreBot**: firma **HMAC-SHA256** (verificar antes de procesar), **reintentos con backoff** (1min→24h, 6 intentos), y el doc recomienda **idempotencia por `id` de webhook** (= tu regla #8) y **procesar async con colas** (= ARQ). Patrón: webhook → verificar HMAC → job ARQ idempotente → evento SSE. Bonus: eventos `payment.*` (listos para #11 si se retoma) y `membership.*` (estado de suscripción/billing).

**Recepción de facturas de proveedores — verificado:** se hace vía el módulo RADIAN ("Sistema de radicación y registro", Res. 000198/2024 v2.0). Dos vías de obtención: **`/import-excel`** (masiva, desde el reporte de "documentos recibidos" del portal DIAN) y **`/import-track-id`** (individual, por CUFE/CUDE → MATIAS trae el documento). **Insight para #2:** el CUFE está en el **QR impreso** de toda factura → escanear el QR es superior al OCR (trae el XML oficial y la deja radicada). **No documentado:** un buzón 100% automático que reciba solo por NIT — confirmar con MATIAS (Swagger/Postman/soporte).

**Documento Soporte — verificado (cierra compras a no obligados):** `POST /ds/document` (residente / no residente, variantes con IVA + RTE IVA y decimales) y `POST /ds/adjustment-note` (notas de ajuste = crédito/débito sobre DS). Cubre las **compras a proveedores no obligados a facturar** (informales, común en ferreterías) para poder deducir costos.

**Único pendiente fiscal de fondo:**
- El **Documento Equivalente Electrónico POS** está en expansión obligatoria 2025-2026 — confirmar cobertura por tenant (MATIAS tiene sección "Facturación y POS").

---

## Modelo de negocio (DIFERIDO — definir con clientes reales)

> **Decisión:** no se fijan precios todavía. El pricing y los tiers se calibran cuando haya uno o más clientes reales y datos de uso (la propia investigación dice que el outcome-based y los tiers se ajustan con uso real). Ideas preservadas para ese momento:

- Pricing por **plan fijo + módulos á la carte** (Loyverse/Leadsales), tier de entrada barato/gratis (Cliengo), facturado en COP.
- Evaluar **outcome-based** para módulos IA (ej. cobro por fiado recuperado, no por mensaje).
- **Telegram = ventaja de costo:** automatización ilimitada sin tarifas por conversación de Meta — comunicarlo como diferenciador.

---

## Secuencia recomendada (resumen visual)

```
Fase 0 (técnico) ──┬─> Fase 1 (#1 cobro fiados, #4 reportes NL, #3 cierre día, #2 OCR, #5 perfil)
                   │         │
                   │         └─> Fase 2 (#6 reposición, #7 cross-sell, #10 voz, #8/#9)
                   │                       │
                   └───────────────────────┴─> Fase 3 (#11 pagos → #12 capital → #13 marketplace)
```

Cada fase financia y profundiza la siguiente; cada paso hace el dato más valioso y la salida más cara para el ferretero.

---

## Decisión del paso 4 — primer módulo a ejecutar

Propuesta: arrancar con **una iniciativa de Fase 1** que reutilice al máximo la infraestructura y entregue valor visible rápido. Las dos mejores candidatas:

- **Opción A — Agente de cobro de fiados (#1):** ataca el dolor #1 de una ferretería con crédito (cartera), reutiliza `abonar_fiado` y feature `fiados`, e introduce el patrón de jobs ARQ recurrentes + plantillas de mensaje que sirve para muchas features futuras.
- **Opción B — Reportes en lenguaje natural (#4):** el menor esfuerzo, valor inmediato para el dueño, reutiliza `generar_reporte` casi tal cual; buen "primer músculo" para el pipeline de intención→herramienta.

Antes de codificar el módulo elegido se hará: ADR de la decisión (`engineering:architecture`), diseño con `engineering:system-design`, TDD (test primero), y `engineering:code-review` apenas haya código — según `.claude/rules/development-workflow.md`.
