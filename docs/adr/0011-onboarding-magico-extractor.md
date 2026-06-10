# ADR 0011 — Onboarding mágico: extractor Fable 5 (insumo natural → manifiesto válido)

> Estado: **Propuesto** (9 jun 2026). El dueño del negocio entrega lo que tiene (fotos de su lista de
> precios, Excel del catálogo, screenshots de su Instagram) y un pipeline de extracción con **Claude
> Fable 5** lo estructura en un **manifiesto válido** (ADR 0007) que el provisionador consume. Ataca el
> cuello real: la fricción de onboardear clientes no-técnicos. Se apoya en 0007 (manifiesto), 0010
> (panel + job en worker) y extiende el manifiesto con el **pack POS declarativo** (catálogo completo).

## Contexto

1. **El destino ya existe.** `tools/manifest/schema.py` (Pydantic, `extra="forbid"`) + `validacion.validar`
   son un *ground truth* determinista, y el job `provisionar_tenant` (worker ARQ, estado en Redis,
   errores sanitizados) ya ejecuta manifiestos. Lo que falta es el **origen**: hoy el manifiesto se
   escribe a mano (o por el form del panel), campo por campo.
2. **El caso retail es el difícil y el valioso.** Punto Rojo tiene **600+ productos** con lógica de
   venta precisa: `permite_fraccion`, `ProductoFraccion` (fracción→decimal→precio_total/unitario),
   precio escalonado por umbral (`precio_umbral`/`precio_bajo_umbral`/`precio_sobre_umbral`), `Alias`
   para fuzzy/bypass. **La calidad de esta data determina si el agente alucina**: un catálogo mal
   sembrado = bot que cotiza mal (el bug de la lija de $400k). Hoy el pack `pos` del registro **no
   tiene loader** (`loader=None`): sus datos nunca fueron declarativos. Onboardear una ferretería
   sigue siendo ETL bespoke.
3. **Restricciones técnicas medidas contra el código:** la capa canónica `core/llm` es solo-texto
   (`Message.content: str`) — visión no pasa por ahí sin extenderla. La extracción es una llamada de
   **plataforma** (no existe empresa aún) — el factory per-tenant (`core/llm/factory.py`) no aplica.
4. **Economía (principio no negociable del handoff):** Fable como compilador de baja frecuencia, y
   **router por dificultad dentro del propio pipeline**: Excel/CSV ≈ todo determinista (centavos);
   fotos impresas → map con Sonnet (~5x más barato); Fable solo para letra a mano difícil y el
   reduce. Pipeline API bien ruteado: **≤ USD $1 típico** por alta. Y la v1 ni eso: el alta es
   asistida (D4), la extracción corre **en la sesión de Cowork** (cubierta por suscripción) →
   **costo marginal API = $0**. El pipeline API pagado es v2, cuando haya volumen self-serve.

## Decisión

### D1 — Pipeline **map → normalize → reduce → validate**, no "una llamada mágica"

Robustez a 600+ productos exige descomponer; una sola llamada gigante es frágil (truncamiento,
alucinación por fatiga de contexto, irreparable si falla a la mitad).

- **MAP (Fable, visión; 1 llamada por imagen/hoja, paralelizable y reintentable):** cada insumo →
  **filas crudas** `{nombre_visto, precios_vistos[], unidad_vista, notas, origen{insumo, posicion},
  confianza}`. Contrato del prompt: **transcribir lo visto, jamás inventar**; lo ilegible → `null` +
  nota. Salida vía tool-use con JSON Schema estricto.
- **NORMALIZE (código determinista, sin LLM):** parseo de precios colombianos ("12.500", "12,5k",
  "$12.500/m"), unidades canónicas, dedupe por nombre normalizado, **detección de outliers** (precio
  >N× la mediana de su categoría → duda, nunca corrección silenciosa).
- **REDUCE (Fable, razonamiento; sobre filas YA estructuradas):** inferir `categoria`,
  `permite_fraccion` y fracciones típicas del gremio (galón→1/4, varilla→1/2…), aliases regionales y
  typos probables, precio escalonado si la lista lo sugiere ("docena a…"). **Toda inferencia se marca
  `inferido: true`**; toda ambigüedad va a `dudas[]`, no se resuelve adivinando.
- **VALIDATE (ground truth determinista):** `Manifiesto.model_validate` + `validar()` (extendida con
  reglas POS, D3). Si falla → **loop de reparación acotado (máx. 2 pasadas)** devolviendo el
  `ErrorManifiesto` (ya agrupa errores legibles) al modelo; si sigue fallando, el borrador sale con
  sus errores marcados para edición humana. **El validador manda; el modelo nunca lo rodea.**

### D2 — Insumos v1: **imágenes + tabulares**; voz e Instagram-por-URL después

Fotos (lista de precios, cuaderno, carta de servicios), **screenshots de Instagram** (cubre el caso IG
sin scraping frágil/contra-ToS) y **Excel/CSV** (el insumo realista para 600+ productos; muchos negocios
tienen uno, aunque sucio — Fable lo interpreta por chunks como "imagen tabular en texto"). PDF = imágenes
por página. **Nota de voz** (transcripción Whisper con `OPENAI_API_KEY` de plataforma → insumo de texto)
queda como fase posterior: suma integración sin ser el camino crítico.

### D3 — El manifiesto crece con el **pack POS declarativo** (prerequisito, valor doble)

`packs.pos` en `tools/manifest/schema.py`: `productos[]` (codigo?, nombre, categoria?, unidad_medida,
precio_venta, iva, permite_fraccion, precio_compra?, escalonado?{umbral, bajo, sobre},
fracciones[]{fraccion, decimal?, precio_total, precio_unitario?}, stock_inicial?) + `aliases[]`
{termino, reemplazo, producto?}. Loader `cargar_pos` (upsert por clave natural: `codigo` si existe,
si no `nombre` normalizado) registrado en `registry.PACKS["pos"]` (hoy `loader=None`). Validación
semántica nueva: precio>0, fracción coherente (decimal×precio_unitario ≈ precio_total), alias→producto
declarado, IVA ∈ {0,5,19}. **Valor doble:** aun sin IA, esto vuelve declarativa la migración de
cualquier retail (incluido re-provisionar Punto Rojo desde manifiesto).

### D4 — Superficie por etapas: **v1 = Cowork asistido (costo $0)**; **v2 = job ARQ en el worker**

**v1 (ahora):** el alta es asistida (el operador presente), así que la extracción corre **en la
sesión de Cowork**: el operador entrega fotos/Excel, Cowork ejecuta el pipeline de D1 dentro de su
propio contexto (visión incluida en la suscripción → sin gasto API), escribe el manifiesto YAML en
`tools/onboarding/` (gitignored), lo valida con el validador real, y el operador lo revisa y aplica
por el panel (`POST /admin/tenants`) o `railway ssh`. Flujo documentado en
`docs/runbook-onboarding-cowork.md`. Lo único que v1 exige codear es el **pack POS (D3)**.

**v2 (cuando haya volumen self-serve):** el mismo pipeline como job `extraer_manifiesto` en el
worker — molde `provisionar_tenant`: estado en Redis con **progreso** (`map 7/20`), TTL, errores
sanitizados, reintento por imagen, upload desde `/admin` (≤25 imágenes, ≤4 MB c/u re-escaladas
client-side; nunca a disco, nunca al log), **router por dificultad** (D5) con presupuesto ≤$1
típico. WhatsApp self-serve = v3. *Rechazado:* extracción síncrona en el request y storage de
objetos nuevo (innecesario hasta v2+).

### D5 — Llamada LLM (v2): **SDK Anthropic directo tras un puerto**, con router por dificultad

`modules/onboarding/` define un puerto mínimo (`ExtractorLLM: Protocol` con
`extraer(imagenes|texto, schema, prompt) -> dict`) e implementación con `AsyncAnthropic` (visión +
tool-use). Inyectable → testeable con fakes sin red. **Router por dificultad:** Excel/CSV →
determinista + Haiku para mapear encabezados; foto impresa → map con `llm_model_extractor_map`
(default Sonnet); letra a mano/baja confianza del map → re-intento con
`llm_model_extractor` (default `claude-fable-5`), que también corre el reduce. Key =
`anthropic_api_key` de plataforma.
*Rechazado por ahora:* volver multimodal `core/llm` (el extractor es una hoja de baja frecuencia y un
solo vendor — la visión ES el motivo de elegir Fable; generalizar sería especular). Si un segundo
caso multimodal aparece, se promueve.

### D6 — Anti-alucinación estructural (lo que hace "robusto" al onboarding)

1. **Nunca inventar:** campo no observado → `null` + entrada en `dudas[]`. El prompt lo exige; el
   reduce lo re-verifica (toda fila del borrador debe trazar a un `origen` del map).
2. **Procedencia por fila:** cada producto cita insumo y posición → la UI de revisión enseña la foto
   recortada junto al dato (verificación humana en segundos).
3. **Cobertura medida:** el map reporta cuántas filas *ve* vs cuántas extrajo; cobertura <100% se
   reporta, no se rellena.
4. **Outliers deterministas:** post-extracción, precios anómalos por categoría → duda obligatoria
   (la lija de $400k muere aquí).
5. **Revisión humana SIEMPRE:** el resultado es un **borrador** (manifiesto + dudas + métricas). Para
   600 productos la UI no pide leer todo: muestra **solo** dudas, baja confianza, outliers e
   inferencias, más un resumen estadístico. Confirmar → `POST /admin/tenants` existente. **El modelo
   jamás dispara `CREATE DATABASE`.** Slug/NIT/email no salen de una foto: los pide el form.

### D7 — Aceptación: **eval dorado con Punto Rojo real**

El catálogo real de Punto Rojo (600+ productos ya en BD) es el ground truth. Eval: fotos reales de
sus listas → pipeline → comparar contra la BD: **precision/recall por campo** (nombre ≥98% match
fuzzy, precio ≥99% exacto — un precio malo es peor que un faltante), fracciones y escalonados
correctos, costo por corrida ≤ USD $2 con el router (≤ $8 forzando Fable en todo), duración ≤ 10
min. El eval corre como script repetible (`tools/eval_extractor.py`) y es quien CALIBRA el router:
mide con qué modelo de map cada tipo de insumo mantiene los umbrales (Sonnet vs Fable por
legibilidad). En v1 el mismo eval sirve para auditar extracciones hechas en Cowork.

## Consecuencias

**A favor:** convierte el alta de un retail de "ETL bespoke de días" a "fotos + revisión de 10 min";
el pack POS declarativo paga solo (migraciones, re-provisioning, tests); el borrador-con-dudas hace
del riesgo de alucinación un flujo de trabajo en vez de un bug en producción; todo reusa panel,
worker, validador y provisionador existentes.

**En contra / costo:** el esquema del manifiesto crece (mantener sync con modelos POS — mitigado por
la prueba de paridad del loader); la v1 depende de que el operador tenga sesión de Cowork (aceptado:
el alta v1 es asistida por definición); en v2, payload de imágenes por Redis es tosco (revisar si
crece) y ≤ USD $1-2 por alta grande con router (aceptado: evento raro, CAC despreciable).

## Alternativas consideradas

- **Una sola llamada Fable con todo el contexto (1M)** — rechazado: frágil a escala catálogo, sin
  reintento parcial, sin procedencia por fila; 1M de contexto no sustituye descomposición verificable.
- **WhatsApp self-serve como v1** — diferido: más superficie (flujo sin tenant, abuso, media de Kapso)
  antes del primer cliente; el panel asistido entrega el mismo wow en la venta.
- **Scraping de Instagram por URL** — rechazado: frágil y contra ToS; screenshots cubren el valor.
- **Auto-provisionar si valida** — rechazado: un dato mal leído llegaría a producción sin ojos; la
  revisión es barata y el form igual debe pedir slug/NIT/email.
