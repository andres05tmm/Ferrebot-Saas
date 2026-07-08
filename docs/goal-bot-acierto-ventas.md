# GOAL — Llevar el bot de ventas de ferrebot-saas (tenant Punto Rojo) a producción acertando ≥ que bot-ventas-ferreteria

> **Documento de contexto para `/goal`.** Reúne objetivo, diagnóstico verificado, plan por fases, plan de pruebas e invariantes. Pensado para pegarse/apuntarse en una corrida autónoma de Claude Code que trabaje hasta dejar el bot listo para producción.
>
> Repos involucrados:
> - **Destino:** `ferrebot-saas` (este repo). El bot vive en `apps/bot/`, la IA en `ai/`, el dominio en `modules/`.
> - **Referencia (producción, acierta bien):** `bot-ventas-ferreteria` (`C:\Users\Dell\Documents\GitHub\bot-ventas-ferreteria`). Solo para portar lógica de dominio.
>
> Todo lo del diagnóstico está verificado contra el código con cita `archivo:línea`. **No repetir el mito de que "el mayorista por umbral se perdió": NO se perdió** (ver §3.1).

---

## 0. Modo de trabajo (para el agente)

1. **Mide antes de tocar.** Primero monta la medición (Fase 0). "Acertar más que el bot viejo" no es opinable: se prueba con replay sobre el mismo corpus.
2. **Mucho del acierto es DATOS, no código.** El motor determinista de precios ya existe; gran parte del gap es completitud del catálogo del tenant (fracciones, escalonado, alias). Verificar datos antes de añadir lógica.
3. **Respeta la filosofía de la SaaS.** El system prompt es deliberadamente *data-free* (los valores del negocio se obtienen por herramientas, no por prompt; ver `ai/turno.py:135`). No "ensucies" el código con conocimiento hardcodeado de un tenant salvo que se decida explícitamente (§5, política de precálculos).
4. **Invariantes primero (TDD).** Para multitenancy, idempotencia y "nada mueve stock/caja sin movimiento" va test-primero (ver `.claude/rules/testing.md`). El resto, código-primero con tests al cierre de fase.
5. **Una fase a la vez:** implementar → correr la suite de la fase → correr el replay → comparar paridad → solo entonces avanzar.

---

## 1. Objetivo y criterios de éxito (cuantitativo + cualitativo)

**Objetivo:** que el bot de Telegram del tenant Punto Rojo en ferrebot-saas registre ventas (y opere facturación, inventario, gastos, caja, fiados) con **igual o mayor acierto** que el bot de producción `bot-ventas-ferreteria`, y quede apto para corte a producción.

### Criterio cuantitativo (paridad medida)
Sobre **el mismo corpus de mensajes reales** (§5.3), corrido por ambos bots:

| Métrica | Meta |
|---|---|
| Acierto en **ventas simples** (1 producto, entero/fracción) | **≥ 97%** y **≥** el del bot viejo |
| Acierto en **ventas** global (incl. multi-producto, mayorista, peso/volumen) | **≥ 90%** y **≥** el del bot viejo |
| Acierto en funciones no-venta (gasto, fiado, abono, consulta) | **≥ 90%** y **≥** el del bot viejo |
| Regresiones en invariantes (aislamiento, idempotencia, stock/caja) | **0** |
| Falsos positivos peligrosos (registrar venta equivocada sin preguntar) | **≈ 0** (los rieles deben atajar) |

> "Acierto" por mensaje = la herramienta correcta se invoca **y** producto correcto **y** cantidad correcta **y** total dentro de tolerancia (la misma tolerancia del riel de precio: 1% o $1, ver §3.1). Definición operacional exacta en §5.4. *(Confirmar metas con Andrés; son propuestas.)*

> **Señal de optimización (anti-overfit) — crítico para /goal:** el rig de medición ya existe en `tests/evals/replay/` (probado: 22/22 en `corpus_seed.jsonl`). Pero ese seed es un **smoke test de la maquinaria, NO el objetivo**. Optimizá SIEMPRE contra el **corpus real** (§5.3). Como ese corpus se reconstruye de las ventas REALES registradas en producción, "reproducir esas ventas" equivale a **igualar al bot viejo**: acierto absoluto y paridad salen de la MISMA corrida, sin re-correr el bot viejo. No subas el número tocando el seed; subilo tocando el bot.

### Criterio cualitativo (checklist de escenarios)
Pasar sin fallos la matriz de escenarios de §5.5 (multi-producto, fracciones, mayorista por umbral, wayper kilo/unidad, puntillas por gramos/pesos/caja, factura electrónica DIAN, inventario, gastos, caja, fiados).

### Gate de producción
Cuantitativo cumplido + checklist verde + suite `pytest` en verde + invariantes cubiertos + runbook de corte de webhook revisado (`docs/migracion-puntorojo.md`, fase de corte).

---

## 1bis. Línea base MEDIDA con datos reales (corrida de replay)

> Corrida real del rig sobre **579 ventas reconstruidas** del catálogo de producción de Punto Rojo (631 productos), pasando cada frase por `ai.bypass` + dispatcher reales. Rig: `tests/evals/replay/`. Extractor de datos reales (catálogo + corpus desde la DB de producción del bot viejo en Railway): `bot-ventas-ferreteria/scripts/extraer_para_replay.py`. **Esta es la señal contra la que /goal debe optimizar — no el `corpus_seed.jsonl`.**

Resultado **segmentado por fidelidad del corpus**:

| categoría | n | acierto | peligrosos | lectura |
|---|---|---|---|---|
| `entero` | 252 | **96.0%** | 0 | núcleo de ventas sólido |
| `fraccion` | 111 | 77.5% | 0 | los misses ≈ deriva de precio histórico (no es bug) |
| `fraccion_reconstruida` | 26 | 84.6% | 0 | reconstrucción de fracciones (1/4, 1/2…) funcionando |
| `mayorista` | 34 | 0% | 0 | el bypass difiere al LLM **por diseño** (ruta LLM pendiente) |
| `no_reconciliado` | 156 | 1.3% | 131 | caja/ciento/gramo: **gap real + artefacto de corpus** |

**Acierto en lo fiel (`entero`+`fraccion`+`reconstruida`): 350/389 ≈ 90%, con CERO registros peligrosos.** El global "60.8%" lo hunde `no_reconciliado`. No perseguir el global a ciegas.

### Conclusión que reordena el plan
El código de ventas de ferrebot-saas **ya está fuerte** (~90% fiel, 96% enteros, cero errores peligrosos en lo representable). Los gaps son acotados y nombrados. **Prioridad real para /goal:**

1. **Unidad caja/ciento/gramo** (puntillas, lijas, tintes a granel) — origen del **100% de los registros peligrosos**. El bot hace `cantidad × precio` ("500 puntilla" → millones); el bot viejo manejaba puntillas por gramos/pesos/caja (`bot-ventas-ferreteria/bypass.py`). **Gap #1.**
2. **Ruta LLM** para lo que el bypass difiere: mayorista (34 casos) y multiproducto. Sin esto, esos casos ni se miden.
3. **Capa de normalización/alias** (G1/G2) — typos/abreviaciones. OJO: el corpus de ventas usa el `alias_usado` ya resuelto, así que este gap **no se ve aquí**; medirlo requiere mensajes reales (`conversaciones_bot`, 45 hoy) o un set curado de la matriz §5.5.
4. (menor) Fracciones: separar fallo real de deriva de precio (etiquetar con el precio histórico de la venta, no con el catálogo de hoy).

### Caveats del corpus (para no malinterpretar el número)
- **Deriva de precio:** las ventas son históricas; el catálogo es el de hoy. Diffs chicos en fracciones/enteros son ruido temporal, no errores del bot.
- **`no_reconciliado`:** la frase reconstruida ("500 PUNTILLA") no es lo que tecleó el vendedor (fue "500 gramos" o "5 cajas"); mezcla gap real de unidad con límite de reconstrucción. Medir caja/ciento de verdad necesita el texto real.
- **Cómo subir el número bien:** tocando el **bot** (unidad caja/ciento, ruta LLM, normalización), NUNCA ablandando el corpus.

---

## 2. Los dos bots en una frase

- **`bot-ventas-ferreteria` (viejo, acierta):** monolito de un solo negocio. Acierta porque mete **mucha inteligencia determinista ANTES y ALREDEDOR del LLM**: normalización de typos/abreviaciones, ~100 alias por defecto, resolución de wayper, un bypass amplio, y un prompt que **inyecta el catálogo candidato + precálculos + avisos de ambigüedad** por cada mensaje.
- **`ferrebot-saas` (nuevo):** multi-tenant, arquitectura limpia (bypass→dispatcher con rieles, RBAC, idempotencia, motor de precios determinista, búsqueda en 4 capas, harness de tests). Pero **se apoya mucho más en el LLM con un prompt casi vacío y una capa de alias/normalización vacía por defecto.** De ahí el menor acierto frente a entradas "sucias" del mundo real.

**Tesis del trabajo:** ferrebot-saas es más seguro y mejor diseñado, pero le falta la "capa de dominio determinista" que hace acertar al viejo. El plan cierra ese gap **sin** romper la arquitectura multi-tenant — preferentemente con datos y con una capa de normalización reutilizable, no con hardcode por tenant.

---

## 3. Diagnóstico verificado del gap

### 3.1 Lo que YA está bien en ferrebot-saas (NO rehacer)

| Capacidad | Dónde | Nota |
|---|---|---|
| Bypass Python → converge al dispatcher (no duplica lógica) | `ai/bypass.py` | Resuelve entero, fracción numérica/escrita y mixta. Bloquea cliente/consulta/modificación y "para Nombre". |
| Dispatcher con rieles, RBAC, capacidades, idempotencia | `ai/dispatcher.py:131-204`, `ai/rieles.py` | Ejecuta una sola vez; corta en `Preguntar`/`Confirmar`. |
| **Riel de producto (R1):** 0 candidatos→preguntar, >1→ambiguo→preguntar | `ai/rieles.py:64-84` | Todo-o-nada: exactamente 1 candidato pasa. |
| **Riel de precio (R2):** cuestiona si total del modelo difiere del catálogo | `ai/rieles.py:88-117`, umbrales en `ai/ports.py:63-64` | Tolerancia **1%** o **$1** (lo mayor). Solo si el precio NO lo dijo el usuario. **Red de seguridad clave que el viejo no tiene.** |
| **Riel de confirmación (R3):** pide OK en gasto/fiado/abono | `ai/rieles.py:121-125` | Default `confirmar_mutaciones=True`. |
| Límites por empresa (monto máx, descuento máx) | `ai/limites.py:73-105` | Modos `confirmar`/`escalar`. |
| **Motor de precios escalonado + fracciones (¡intacto!)** | `modules/inventario/models.py:33-39,56-66`, `modules/inventario/precios.py:31-71` | Cascada **escalonado → fracción → simple** en `obtener_precio_para_cantidad`. El **mayorista por umbral NO se perdió.** |
| Búsqueda en 4 capas | `modules/inventario/busqueda.py` | Exacta → alias → trigram (`UMBRAL_TRIGRAM=0.3`) → fuzzy (`UMBRAL_FUZZY=92`, solo sugiere). |
| Harness de tests sin Telegram + bases efímeras Postgres | `tests/conftest.py`, `tests/evals/_harness.py`, `tests/test_bot_webhook.py` | Ya hay tests de "paridad". |

> Implicación: **el motor determinista acierta si los datos del tenant están completos.** Si Wayper/Thinner/Acronal tienen sus fracciones o su escalonado bien cargados, `obtener_precio_para_cantidad` los resuelve correctamente sin hardcode. Por eso la Fase 1 (datos) es tan importante como el código.

### 3.2 Gaps reales (verificados), priorizados

**G1 — Capa de normalización pre-LLM ausente (alto impacto).**
El viejo corre, antes de todo, `aplicar_alias_completo` (`bot-ventas-ferreteria/alias_manager.py:386-400`): normaliza `#N`→`N°N`, `s.c.`→`sin cabeza`, `t-N`→`tN`; aplica regex (p. ej. `lija $120`→`lija #120`, `puntilla 2 sc`→`puntilla 2 sin cabeza`, `medio galón thinner`→`0.5 galones thinner`) y lambdas con cálculo (`botellita thinner`→`thinner 4000`, `bolsa carbonato`→`carbonato 25 kg`). **ferrebot-saas no tiene nada de esto**: los typos/abreviaciones llegan crudos y dependen de trigram/fuzzy o del LLM. Es determinista y barato; hoy falta.

**G2 — Tabla `aliases` vacía por defecto (alto impacto).**
En ferrebot-saas la tabla `aliases` nace vacía por tenant (`modules/inventario/models.py:74` lo documenta; migración `0001_tenant_init` no siembra). El viejo tiene **~100 alias por defecto** (`_ALIASES_DEFAULT`, `alias_manager.py:26-128`: `tiner→thinner`, `drwayll/drwall/...→drywall`, `sc→sin cabeza`, cuñetes davinci, `waiper→wayper`, etc.) más los alias por producto (`productos.aliases`). Hay que **sembrarlos** en el tenant (migrar `productos.aliases` del dump + cargar los universales).

**G3 — System prompt minimalista (impacto medio-alto).**
`ai/turno.py:130-162`: ~7 líneas (~200-260 tokens). Tiene la regla de fracciones ("NUNCA dividir el galón") y "vende aunque el stock marque 0", pero **sin few-shot, sin candidatos inyectados, sin precálculos, sin nudge de ambigüedad** (la ambigüedad la maneja solo R1). El viejo, en cambio, inyecta por mensaje el bloque `MATCH:` con los candidatos y sus precios/fracciones y los avisos de ambigüedad (`bot-ventas-ferreteria/ai/prompt_products.py`). En la SaaS el modelo "ve" el catálogo solo si llama `consultar_producto`. Riesgo: que no consulte, o que invente totales (R2 atrapa el total, pero no atrapa elegir el producto equivocado entre variantes si R1 ya lo dejó con 1 candidato malo).

**G4 — Bypass más estrecho (impacto medio; sube costo/latencia y carga al LLM).**
ferrebot-saas manda al modelo: **multi-producto** (coma o salto de línea → `CaeAlModelo`), **productos con escalonado** (gate `ai/bypass.py:267`: `if prod.esquema.tiene_escalonado: return None`), y no tiene los casos especiales del viejo: **puntillas por gramos/pesos/caja**, **wayper kilo/unidad**, **docenas**. El viejo bypassa todo eso determinísticamente (`bot-ventas-ferreteria/bypass.py`).

**G5 — Completitud de datos del tenant (bloqueante para medir acierto).**
El acierto del motor depende de que el dump real esté bien cargado: 632 productos, **722 fracciones de precio**, escalonado donde aplique, y `productos.aliases` (`docs/migracion-puntorojo.md:15`). Verificar que tras `pg_restore` esos campos quedan poblados y que `consultar_producto` los devuelve.

**G6 — Precálculos especiales: inexistentes (decisión de política, §5).**
El viejo hardcodea tablas y fórmulas para Acronal/Thinner/Varsol/Wayper-kilo/tornillos-drywall e inyecta "USA cantidad=X, total=Y SIN MODIFICAR" (`prompt_products.py:971-1204`). ferrebot-saas no tiene equivalente; calcula vía datos. **Recomendado: cerrar esto con DATOS (fracciones/escalonado) + el riel R2 como red, no con hardcode por tenant.** Ver §5.

---

## 4. Plan por fases (para `/goal`)

> Cada fase termina con: tests de la fase en verde + medición de replay + comparación de paridad. No avanzar si baja la paridad.

**Fase 0 — Montar la medición (primero).**
- Sembrar base real: `python -m tools.provision_from_manifest --from tools/onboarding/puntorojo.json` y luego `pg_restore` del dump más reciente en `backups/` (ver §5.2). *(No exponer secretos del manifiesto en logs ni en git.)*
- Construir el script de replay (§5.1–5.4) sobre el harness en proceso (`tests/evals/_harness.py:construir`) para la ruta bypass y `manejar_update` con tenant real para la ruta LLM.
- Conseguir el corpus (§5.3) y etiquetar el "esperado".
- **Salida:** reporte base de acierto de ferrebot-saas y del bot viejo sobre el mismo corpus (la línea de partida).

**Fase 1 — Completitud de datos del tenant (G5).**
- Verificar que el dump pobló fracciones, escalonado y `productos.aliases`. Donde falte, completar el ETL (`docs/decisiones-migracion.md`, `docs/migracion-puntorojo.md`).
- Re-medir: cuánto sube el acierto solo por datos.

**Fase 2 — Capa de normalización pre-LLM + alias del tenant (G1, G2).**
- Portar un **normalizador universal** (typos generales, abreviaciones, notación de lija, `sc/cc`, expansión de unidades, resolución wayper kilo/unidad) como módulo compartido aplicable a todos los tenants, idealmente como paso previo del bypass y de la búsqueda. Fuente: `bot-ventas-ferreteria/alias_manager.py` (`_ALIASES_DEFAULT`, `_ALIAS_REGEX`, `_ALIAS_LAMBDA`, `_resolver_wayper`).
- Sembrar la tabla `aliases` del tenant: migrar `productos.aliases` del dump + cargar los universales relevantes.
- **Decisión de diseño:** universal en código (typos genéricos) vs por-tenant en datos (alias de producto específicos como "cuñete davinci"). Recomendado: dos niveles (código universal + datos por tenant).

**Fase 3 — Enriquecer el contexto del modelo (G3).**
- Opción A (más fiel al viejo): inyectar por mensaje un bloque `MATCH:` con candidatos + precios/fracciones + nudge de ambigüedad Clase A (numérica) / Clase B (color/palabra). Portar de `prompt_products.py:141-232` (`_detectar_ambiguedad_variante`, `_nudge_ambiguo`) y el formato de `_linea_candidato:676-709`.
- Opción B (más fiel a la SaaS): mantener prompt data-free pero (a) endurecer la instrucción de "consulta SIEMPRE antes de cotizar/registrar", (b) enriquecer la respuesta de `consultar_producto` con fracciones y escalonado, (c) añadir few-shot de los 5-8 patrones más frecuentes.
- **Recomendado:** B como base + el nudge de ambigüedad de A (es lo que más previene "adivinar la variante"). Medir ambas si hay dudas.

**Fase 4 — Ampliar cobertura del bypass (G4).**
- Multi-producto (dividir por coma/salto y bypassar si todos los ítems son bypasseables, convergiendo al dispatcher con un solo `registrar_venta` multi-línea).
- Casos especiales por unidad: puntillas por gramos/pesos/caja, docenas. Wayper queda resuelto por el normalizador (Fase 2) + escalonado/fracciones (datos).
- Mantener el gate de escalonado solo si el modelo lo resuelve mejor; si no, permitir bypass con `obtener_precio_para_cantidad`.

**Fase 5 — Política de precálculos (G6).**
- Confirmar enfoque data-driven (preferido). Asegurar que productos por peso/volumen (Acronal, Thinner, Varsol, Wayper) tengan fracciones/escalonado correctos en datos; dejar que el motor + R2 hagan el resto.
- Solo si la data no puede representar un caso, considerar un "precálculo" genérico guiado por datos (no por nombres hardcodeados).

**Fase 6 — Funciones no-venta (paridad por checklist).**
- **Factura electrónica DIAN (MATIAS):** emisión async + idempotencia + reintentos; cuidado con `city_id` (es ID interno de MATIAS, no DANE — ver regla #10 de `bot-ventas-ferreteria/CLAUDE.md` y `docs/facturacion-dian.md`). El secret/credenciales viven cifrados en control DB / manifiesto; nunca en código.
- **Inventario** (consultas, stock), **gastos**, **caja** (apertura/cierre/balance), **fiados** (crear/abonar): correr el checklist §5.5 y los tests de cada módulo.

**Fase 7 — Endurecer y cerrar.**
- Revisar rieles/límites con los casos que el replay marque como riesgosos.
- Medición final de paridad; checklist verde; `pytest` verde; runbook de corte.

---

## 5. Plan de pruebas (replay automatizado + checklist)

### 5.1 Mecanismo (verificado)
Tres formas de "mandarle mensajes al bot" sin Telegram (de más a menos rápida para iterar):

1. **Eval harness en memoria** — `tests/evals/_harness.py:construir(...)` arma repos en memoria + el `Dispatcher` real, sin DB ni LLM. Ideal para la **ruta bypass** (cientos de frases por segundo, costo cero).
   ```python
   h = construir(productos_reales)
   res = await h.bypass.intentar(frase, ctx, h.recursos)
   header = h.ventas_repo.ultimo_header   # args de registrar_venta
   ```
2. **`manejar_update` en proceso con fakes** — patrón de `tests/test_bot_webhook.py:150-164`. Sin red; usa `FakeResolver/FakeSecretos/...` y un `FakeNotificador` para capturar el texto. Para la **ruta LLM** se corre contra un tenant real sembrado (`tests/test_bot_tenant_integration.py:141-157`).
3. **Webhook HTTP real** — `POST /tg/{slug}` con header `X-Telegram-Bot-Api-Secret-Token` y payload de update de Telegram crudo (`apps/bot/webhook.py:42-61,177`). Más fiel de punta a punta pero exige control DB + Redis (dedup) + secret configurado. Reservar para un smoke final.

**Payload mínimo de un mensaje de vendedor** (para #3, o para construir `UpdateBot`):
```json
{ "update_id": 100, "message": { "message_id": 1, "from": {"id": 555}, "chat": {"id": 555}, "text": "2 martillo" } }
```

**Captura del resultado:** `FakeNotificador.enviados` (texto de respuesta) y `ventas_repo.ultimo_header` (tool call + args). También se persiste el turno en la tabla `conversaciones_bot` del tenant (`modules/memoria/models.py:16-25`), legible con `SELECT rol, contenido FROM conversaciones_bot ORDER BY creado_en DESC`.

**Recomendado:** bypass por el harness en memoria (#1); frases que caen al modelo, por `manejar_update` con tenant real sembrado (#2). Webhook HTTP (#3) solo como smoke de cierre.

### 5.2 Datos reales del tenant
- Manifiesto: `tools/onboarding/puntorojo.json` (no volcar sus secretos a logs/git).
- Dumps de producción versionados: `backups/*/ferrebot_puntorojo.dump` (usar el más reciente). Contienen 632 productos, 722 fracciones, 228 ventas, 481 líneas, 59 clientes (`docs/migracion-puntorojo.md:15`).
- Sembrado:
  ```bash
  python -m tools.provision_from_manifest --from tools/onboarding/puntorojo.json
  pg_restore --clean --if-exists -d ferrebot_puntorojo backups/<más-reciente>/ferrebot_puntorojo.dump
  # + setval de secuencias (docs/migracion-puntorojo.md)
  ```
- Requisitos de entorno: Postgres accesible; `admin_database_url` y `tenants_direct_url_base` en `core/config` (`tests/conftest.py:32,64`). Para la ruta LLM, la clave del proveedor configurada por empresa.

### 5.3 Corpus de mensajes reales (input que falta — acción de Andrés)
- **Fuente ideal:** tabla `conversaciones_bot` de **producción** (`bot-ventas-ferreteria`), filas `role='user'` = texto textual del vendedor (`migrations/018_conversaciones_bot.py`). **No está en el repo**; vive en la DB de Railway. Exportar:
  ```bash
  railway run pg_dump $DATABASE_URL --table=conversaciones_bot -f conversaciones_bot.dump
  ```
- **Fallback (alta fidelidad para ventas simples):** reconstruir mensajes desde `ventas_detalle.alias_usado` + `cantidad` (`migrations/003_migrate_ventas.py:283` guarda el alias que usó el vendedor). Ej.: `alias_usado='martillo', cantidad=2` → `"2 martillo"`. No sirve para gastos/fiados/consultas (sin texto original).
- **Etiquetado:** por cada mensaje, el "esperado" = `{herramienta, producto_id, cantidad, total, metodo_pago}` derivado de la venta real registrada en esa fecha (`ventas`/`ventas_detalle`).
- **Tamaño objetivo:** ≥300 ventas reales (mezcla simple/fracción/mayorista/peso) + ≥50 por cada otra función. Mantener un set "congelado" para no medir sobre datos que el agente vio al ajustar.

### 5.4 Métrica (definición operacional)
Por mensaje: `acierto = (tool esperado == tool emitido) ∧ (mismo producto_id) ∧ (misma cantidad) ∧ (|total_emitido − total_esperado| ≤ max(1% · total_esperado, $1))`.
Reportar: acierto global, **desglose por categoría** (simple, fracción, mixta, mayorista, peso/volumen, multi-producto, gasto, fiado, abono, consulta) y **paridad** = acierto(ferrebot-saas) − acierto(bot-viejo) sobre el MISMO corpus. Registrar también: % que cayó al LLM vs bypass, y casos donde un riel preguntó (no es fallo: es seguridad).

### 5.5 Checklist cualitativo (matriz de escenarios)
Cada fila debe pasar (registrar correcto, o preguntar cuando debe preguntar):

| Función | Escenarios mínimos |
|---|---|
| Venta simple | entero ("3 tornillo"); unidad de empaque ("galón de esmalte blanco") |
| Fracción | "1/2 vinilo", "medio galón thinner", mixta "1-1/2 vinilo"; verificar que NO divide el galón |
| Mayorista por umbral | cantidad < umbral vs ≥ umbral → precio correcto |
| Peso/volumen | wayper kilo vs unidad; acronal 1/2 kg; thinner por litro/botella |
| Puntillas | por gramos, por pesos ("$2000 de puntilla 2 sc"), por caja |
| Multi-producto | "3 tornillo, 2 chazo" y en saltos de línea |
| Ambigüedad | "1 lija" sin número → **pregunta**, no adivina; vinilo sin color → pregunta |
| Cliente/Fiado | "para Juan", "fiado a nombre de…" → flujo cliente/fiado, pide confirmación |
| Gasto | "gasto 20000 almuerzo" → confirma y registra |
| Caja | apertura, cierre, balance del día |
| Factura electrónica | emisión DIAN vía MATIAS, idempotente, `city_id` correcto |
| Inventario | consulta de stock/precio sin registrar venta |
| Modificación | "cancela la última", "corrige…" → no registra venta nueva |

---

## 6. Invariantes no negociables (recordatorio)

- **Aislamiento multi-tenant:** toda operación resuelve el tenant y usa su sesión; jamás cruzar datos (ver `.claude/rules/multitenancy.md`). **TDD test-primero.**
- **Idempotencia** en venta/factura/webhooks (UNIQUE `idempotency_key`). **TDD test-primero.**
- **Nada mueve stock sin movimiento de inventario, ni caja sin movimiento de caja.** **TDD test-primero.**
- **Zona horaria Colombia (UTC-5)** siempre; nunca `date.today()` crudo.
- **Secretos cifrados**, nunca en código/git/logs (manifiesto incluido).
- **Acceso a datos solo por repositorios**; `async/await` en endpoints con eventos; logging estructurado con `tenant_id`/`request_id`.

---

## 7. Inputs que necesito de Andrés / decisiones abiertas

1. **Corpus + catálogo reales (paso que desbloquea todo):** el rig de medición ya está en `tests/evals/replay/` (probado: 22/22 en el seed). Falta la señal real — exportar el catálogo del tenant (`extraer_catalogo.py`) y el corpus de producción (`extraer_corpus.py --modo ventas`, que reconstruye las ventas reales = ground truth del bot viejo; `--modo conversaciones` para el texto literal de los casos complejos). La DB de producción vive en Railway, no en el repo. Patrón oro: mide acierto y paridad a la vez.
2. **Entorno de ejecución:** ¿hay Postgres local/Docker para sembrar el dump y correr el harness? ¿claves del proveedor LLM disponibles para la ruta no-bypass?
3. **Política de precálculos (§5/G6):** confirmar enfoque **data-driven** (recomendado) vs portar precálculos hardcodeados del viejo.
4. **Estrategia de prompt (Fase 3):** A (inyectar MATCH+nudge, como el viejo) vs B (data-free + consultar_producto enriquecido + few-shot) vs híbrido (recomendado).
5. **Definición de `/goal`:** pegar el comando/su contrato para adaptar el formato del "purpose" de §8.

---

## 8. "Purpose" listo para `/goal` (borrador)

> Objetivo: Dejar el bot de Telegram del tenant **Punto Rojo** en **ferrebot-saas** listo para producción, registrando ventas y operando facturación/inventario/gastos/caja/fiados con **acierto igual o mayor** al bot de producción `bot-ventas-ferreteria`, medido por **replay** sobre el **mismo corpus real**.
>
> Reglas: respeta los invariantes (aislamiento multi-tenant, idempotencia, "nada mueve stock/caja sin movimiento", TZ Colombia, secretos cifrados, repos-only) — esos van test-primero. No metas valores del negocio en el system prompt (la SaaS es data-free); cierra el gap con **datos** (catálogo/fracciones/escalonado/alias completos) y una **capa de normalización reutilizable**, no con hardcode por tenant.
>
> Procede por fases (0→7 de `docs/goal-bot-acierto-ventas.md`): **mide primero** (siembra el dump real + monta el replay), completa datos, porta normalización/alias, enriquece el contexto del modelo, amplía el bypass, resuelve precálculos por datos, cubre funciones no-venta, endurece. Al cierre de cada fase: corre `pytest`, corre el replay y **no avances si baja la paridad**. Criterio de hecho: ventas simples ≥97% y todo ≥ el bot viejo, checklist de escenarios verde, suite verde, cero regresiones de invariantes.
>
> Referencia de lógica a portar (solo dominio): `bot-ventas-ferreteria/alias_manager.py`, `bypass.py`, `ai/prompt_products.py`. Diagnóstico y citas: `docs/goal-bot-acierto-ventas.md`.

---

### Apéndice — rutas clave

**ferrebot-saas:** `ai/turno.py` (prompt) · `ai/tools.py` (herramientas, `RegistrarVentaArgs:93`) · `ai/bypass.py` (`gate escalonado:267`) · `ai/dispatcher.py` · `ai/rieles.py` · `ai/limites.py` · `ai/ports.py:63-64` (tolerancias) · `modules/inventario/models.py:33-66` · `modules/inventario/precios.py:31-71` · `modules/inventario/busqueda.py` · `tests/conftest.py` · `tests/evals/_harness.py` · `tools/onboarding/puntorojo.json` · `backups/*/ferrebot_puntorojo.dump` · `docs/migracion-puntorojo.md` · `docs/decisiones-migracion.md` · `docs/facturacion-dian.md`.

**bot-ventas-ferreteria (referencia):** `alias_manager.py:26-128` (alias) · `:135-199` (regex/lambda) · `:315-345` (wayper) · `bypass.py:41-66` (bloqueantes) · `:274-580` (`intentar_bypass_python`) · `:591-618` (mayorista) · `ai/prompt_products.py:141-232` (ambigüedad) · `:676-709` (serialización MATCH) · `:971-1204` (precálculos) · `migrations/018_conversaciones_bot.py` · `migrations/003_migrate_ventas.py:283` (`alias_usado`).
