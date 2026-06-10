# Plan — Onboarding mágico (ADR 0011): fases + prompts para Claude Code

> 9 jun 2026. Ejecuta el ADR 0011. Patrón: un prompt por fase → Claude Code (Fable 5) implementa →
> Cowork revisa el diff real → CI verde → merge. Con Fable 5 en Claude Code las fases pueden ser
> grandes, pero **siguen siendo fases**: un PR gigante es irreviewable y las reglas críticas
> (multitenancy, secretos, repositorios) se verifican por corte. F1 y F2 son independientes →
> pueden correr en paralelo (dos ramas).

## Mapa de fases — v1 con costo API $0 (ADR 0011 §D4)

**v1 (ahora):** la extracción la hace Cowork en sesión (suscripción, no API). Solo se codea el riel.

| Fase | Entrega | Depende de |
|---|---|---|
| F1 | Pack POS declarativo: schema + validación + loader + paridad | — |
| F1b | CLI `--check` (validar manifiesto sin provisionar) + runbook Cowork | F1 |
| F5 | Eval dorado Punto Rojo (audita extracciones de Cowork; calibra v2) | F1 |

**v2 (cuando haya volumen self-serve; los prompts de abajo quedan listos):**

| Fase | Entrega | Depende de |
|---|---|---|
| F2 | Núcleo extractor: puerto LLM + router por dificultad + CLI | F1 |
| F3 | Job ARQ `extraer_manifiesto` + endpoints `/admin/onboarding` | F2 |
| F4 | Panel: wizard subir → progreso → revisión de dudas → prefill crear-tenant | F1, F3 |
| F6 (opc.) | Nota de voz (Whisper → insumo texto) | F3 |

Criterio de "hecho" de v1: dar de alta una ferretería ficticia desde 3 fotos + 1 Excel sucio en
<15 min y **USD $0 de API**, con el bot cotizando fracciones correctamente al primer mensaje.

---

## F1 — Pack POS declarativo (sin IA; valor propio)

**Prompt para Claude Code:**

```
Lee docs/adr/0011-onboarding-magico-extractor.md (§D3) y docs/adr/0007. Implementa el pack POS
declarativo del manifiesto:

1. tools/manifest/schema.py: agrega PackPos a Packs (sección `packs.pos`): productos[] {codigo?,
   nombre, categoria?, unidad_medida, precio_venta (entero pesos), iva, permite_fraccion=False,
   precio_compra?, escalonado? {umbral, bajo, sobre}, fracciones[] {fraccion, decimal?, precio_total,
   precio_unitario?}, stock_inicial?} y aliases[] {termino, reemplazo, producto?}. Mismo estilo del
   módulo: tipos permisivos donde la regla rica va en validacion.py, extra="forbid", docstrings en
   español.
2. tools/manifest/validacion.py: reglas POS reuniendo TODOS los errores: precio_venta>0, iva ∈
   {0,5,19}, fracciones solo si permite_fraccion, coherencia decimal×precio_unitario≈precio_total
   (tolerancia 1 peso) cuando ambos existan, escalonado completo o ausente, alias.producto → producto
   declarado, nombres de producto únicos (normalizados), coherencia flag↔datos para `pos` como ya se
   hace con agenda/faq.
3. tools/manifest/packs/pos.py: loader cargar_pos(seccion, conn) -> dict[str,int], upsert idempotente
   por clave natural (codigo si existe, si no nombre normalizado lower/trim) sobre productos,
   productos_fracciones, aliases e inventario (stock_inicial → fila de inventario + movimiento
   ENTRADA con idempotency_key derivada del manifiesto; regla 7 de CLAUDE.md: nada toca stock sin
   movimiento). Mira tools/manifest/packs/agenda.py para el patrón y modules/inventario/models.py
   para columnas reales.
4. tools/manifest/packs/registry.py: PACKS["pos"].loader = cargar_pos.
5. Actualiza el *.example.yaml con una sección pos de ejemplo (3 productos: uno simple, uno con
   fracciones, uno escalonado).
6. Tests (TDD, BD efímera como los tests de loaders existentes): validación (cada regla nueva),
   loader idempotente (correr 2 veces = mismas filas), y PARIDAD: sembrar por manifiesto un
   mini-catálogo y verificar filas idénticas a un insert directo esperado.

Reglas: acceso a datos del loader con psycopg como los demás loaders (es tool de provisioning);
nunca print; zona horaria Colombia si tocas timestamps. No toques el provisionador ni el worker.
```

**Revisión Cowork:** upsert realmente idempotente (claves naturales, no ids), movimiento de
inventario presente, ningún `empresa_id` en tablas de negocio.

---

## F1b — CLI `--check` + runbook del flujo Cowork (v1, costo $0)

**Prompt para Claude Code:**

```
Dos entregas chicas sobre lo existente:

1. tools/provision_from_manifest.py: flag --check — carga + valida el manifiesto (cargar_manifiesto
   + validar) e imprime "VALIDO" o el ErrorManifiesto agrupado, SIN tocar ninguna base. Exit code
   0/1. Test del flag.
2. Verifica que docs/runbook-onboarding-cowork.md (lo escribe Cowork) referencie comandos que
   existen tal cual; ajusta nombres si algo difiere.
```

El runbook `docs/runbook-onboarding-cowork.md` define el flujo operativo: Andrés entrega
fotos/Excel a Cowork en sesión → Cowork ejecuta map/normalize/reduce en su contexto (contrato
anti-alucinación del ADR §D6: transcribir, no inventar; dudas explícitas; outliers señalados) →
escribe `tools/onboarding/<slug>.yaml` → corre `--check` → presenta a Andrés SOLO dudas/outliers/
inferencias + resumen → Andrés corrige/confirma → aplica por panel o `railway ssh`.

---

## F2 — Núcleo extractor (v2; puro, testeable, con CLI de demo)

**Prompt para Claude Code:**

```
Lee docs/adr/0011-onboarding-magico-extractor.md (§D1, D5, D6). Crea modules/onboarding/ con el
pipeline de extracción SIN tocar worker ni API aún:

1. modules/onboarding/puertos.py: ExtractorLLM(Protocol) con un método
   `async extraer(*, bloques_contenido: list[dict], system: str, schema_tool: dict) -> dict` —
   bloques en formato contenido Anthropic (image/text). Implementación AnthropicExtractor (SDK
   AsyncAnthropic, import perezoso como core/llm/providers/claude.py, tool-use forzado con
   tool_choice). ROUTER por dificultad (ADR §D5): settings `llm_model_extractor_map` (default
   Sonnet) para el map de insumos impresos/tabulares y `llm_model_extractor` (default
   "claude-fable-5") para reduce + re-intento de filas con confianza < umbral o letra a mano.
   Key anthropic de plataforma. Reintento simple (2) con backoff en errores de red/429.
2. modules/onboarding/tipos.py: FilaCruda (nombre_visto, precios_vistos, unidad_vista, notas,
   origen{insumo_idx, posicion}, confianza), Duda, MetricasExtraccion (filas_vistas,
   filas_extraidas, tokens, costo_estimado), BorradorManifiesto (manifiesto_dict, dudas,
   inferencias, metricas, errores_validacion).
3. modules/onboarding/prompts.py: prompts de MAP (transcribir lo visto, NUNCA inventar, ilegible →
   null+nota, reportar cuántas filas ve) y REDUCE (inferencias marcadas, dudas en vez de adivinar,
   toda fila traza a un origen). En español, con 2-3 ejemplos few-shot de ferretería (fracciones,
   "docena a", precios colombianos).
4. modules/onboarding/normalizar.py (PURO, sin LLM): parseo de precio colombiano ("12.500",
   "12,5k", "$ 12.500/m"), unidades canónicas, normalización de nombre para dedupe, detección de
   outliers por categoría (> Nx mediana → Duda). Property-based tests si hay hypothesis; si no,
   tabla de casos amplia.
5. modules/onboarding/service.py: extraer_borrador(insumos, extractor: ExtractorLLM) ->
   BorradorManifiesto. Orquesta: map por insumo (asyncio.gather con semáforo 4, reintento por
   insumo) → normalize → reduce (chunks de ≤200 filas si el catálogo es grande) → ensamblar dict
   de manifiesto (solo packs; identidad/admin los pone el form después) → Manifiesto.model_validate
   parcial + validar() → si falla, UNA pasada de reparación devolviendo el texto del
   ErrorManifiesto al modelo → BorradorManifiesto con todo (incluidos errores residuales).
6. tools/extraer_manifiesto.py: CLI — --imagenes *.jpg --excel archivo.xlsx --salida borrador.yaml;
   imprime resumen (filas, dudas, cobertura, costo). Para demos y para el eval de F5.
7. Tests con FakeExtractorLLM (fixtures de respuestas): pipeline completo, loop de reparación,
   outliers → dudas, cobertura reportada. CERO red en tests.

Excel/CSV: parsear con openpyxl/csv a texto tabular por chunks y tratarlo como insumo de texto del
map (mismo contrato de FilaCruda). Logging estructurado con get_logger("onboarding"); jamás loguear
contenido de insumos.
```

**Revisión Cowork:** el validador es quien manda (ninguna ruta donde el dict del modelo se acepte
sin `validar()`), inferencias marcadas, semáforo y reintentos por-insumo, costo estimado calculado
de `usage` real.

---

## F3 — Job en worker + API `/admin/onboarding`

**Prompt para Claude Code:**

```
Lee docs/adr/0011 (§D4) y el molde apps/worker/jobs.py::provisionar_tenant + modules/admin/router.py.

1. apps/worker/jobs.py: job extraer_manifiesto(ctx, insumos_payload, job_id) — estados en Redis vía
   una clase EstadoExtraccion (molde EstadoProvision, prefijo "extraccion:estado:") con PROGRESO
   (fase, completados/total), resultado = BorradorManifiesto serializado (sin las imágenes), TTL de
   settings. Errores sanitizados por categoría (molde _sanitizar_error); el contenido de insumos
   JAMÁS al log. Registra el job en apps/worker/main.py.
2. modules/admin/onboarding_router.py (mismo gate require_platform, prefijo /admin/onboarding):
   - POST /extracciones: multipart (imágenes y/o excel). Valida: ≤25 archivos, ≤4MB c/u, tipos
     permitidos (jpeg/png/webp/xlsx/csv). Encola con payload base64 y devuelve {job_id} (molde
     crear_tenant: marcar "encolado" antes de encolar).
   - GET /extracciones/{job_id}: estado + progreso + (si ok) el borrador completo.
3. Incluye el router en el registro de routers junto a admin (exento de TenantMiddleware igual que
   /admin).
4. Tests: router con enqueuer fake (validaciones de tamaño/tipo, 202, estado), job con
   FakeExtractorLLM (transiciones encolado→corriendo→ok / →error sanitizado, progreso visible).

El seam del extractor en el worker se inyecta en on_startup como los demás (ctx["extractor"]).
async/await correcto en todo endpoint.
```

**Revisión Cowork:** límites de upload aplicados server-side, nada del insumo en logs/estado,
router exento pero gateado, progreso real (no solo corriendo/ok).

---

## F4 — Panel: wizard de revisión

**Prompt para Claude Code:**

```
Lee docs/adr/0011 (§D6.5) y dashboard/src/pages/admin/. Agrega al panel /admin el flujo "Onboarding
mágico":

1. Paso 1 — Subir: dropzone (imágenes/excel), re-escalado client-side a ≤4MB, POST
   /admin/onboarding/extracciones, polling del estado con barra de progreso (map 7/20).
2. Paso 2 — Revisar: el borrador NO se muestra entero; tres bandejas: (a) dudas + outliers +
   inferencias (cada una con el dato, el origen y la imagen recortada si hay posicion), (b) errores
   de validación residuales, (c) resumen estadístico (n productos, rangos de precio por categoría,
   cobertura). Edición inline de las filas observadas; aceptar/rechazar inferencias en lote.
3. Paso 3 — Completar y crear: form con identidad/admin/canal (slug, NIT, email, phone_number_id —
   lo que una foto no trae), prefill del resto desde el borrador, y submit al POST /admin/tenants
   EXISTENTE (no crear otro camino de provisioning). Mostrar el detail del 422 si rechaza.
4. Reusar componentes ui/ existentes y el patrón de polling del CrearTenantForm. Sin estado
   sensible en localStorage.

Tests del frontend al nivel que ya tenga el dashboard (si no hay infra de tests de front, smoke
manual documentado en el PR con capturas).
```

**Revisión Cowork:** el submit final va al endpoint existente, secretos nunca en localStorage,
la bandeja de dudas es utilizable con 600 productos (paginada/filtrable).

---

## F5 — Eval dorado Punto Rojo (la definición de "robusto")

**Prompt para Claude Code:**

```
Lee docs/adr/0011 (§D7). Crea tools/eval_extractor.py:

1. Entrada: directorio de insumos (fotos/excel reales) + ground truth (export CSV del catálogo real
   o un YAML esperado). Corre el pipeline de F2 (CLI/servicio) y compara: precision/recall por campo
   (nombre con match fuzzy ≥0.9, precio EXACTO, unidad, permite_fraccion, fracciones, escalonado),
   cobertura, n dudas, costo (de usage) y duración.
2. Salida: reporte markdown + JSON (para comparar corridas). Umbrales del ADR: nombre ≥98%, precio
   ≥99%, costo ≤ USD 8, duración ≤ 10 min — el script marca PASS/FAIL por umbral.
3. Flag --map-model para correr el mismo eval con el map en Sonnet (knob de costo del ADR §D7) y
   comparar tablas lado a lado.
4. NO entra al CI (necesita red y key); documentar en docs/runbook.md cómo correrlo y dónde viven
   los insumos dorados (gitignored, junto a tools/onboarding/).
```

**Revisión Cowork:** métricas calculadas sobre el manifiesto final (post-validación), no sobre el
map crudo; el reporte enseña los misses concretos (para iterar prompts con ejemplos reales).

---

## F6 (opcional) — Nota de voz

Transcripción Whisper (`OPENAI_API_KEY` plataforma) → insumo de texto del mismo pipeline. Prompt
corto cuando lleguemos; no bloquea nada.

## Riesgos y mitigaciones

- **Esquema manifiesto ↔ modelos POS divergen** → prueba de paridad de F1 + validación Pydantic
  rompen en CI.
- **Costo por corrida se dispara** (fotos enormes, repair loops) → tope de pasadas (2), re-escalado
  client-side, semáforo, costo reportado en métricas y visible en el panel.
- **Letra a mano ilegible** → el contrato "null + duda" degrada a revisión humana, nunca a datos
  inventados; el eval de F5 mide cuánto queda en dudas (si >20%, iterar prompts/few-shots).
- **Redis con payloads grandes** → tope 25×4MB y TTL corto; si un cliente real lo excede, ahí sí
  storage de objetos (decisión nueva, no antes).
