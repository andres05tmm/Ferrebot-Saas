# ADR 0024 — Gobierno de agentes: rate-limit, presupuesto, prompt caching, evals LLM y métricas

- **Estado:** Aceptado
- **Fecha:** 2026-07-03
- **Relacionados:** ADR 0005 (LLM por empresa), ADR 0023 (resiliencia LLM + saneamiento, Fase 0),
  `core/llm/medicion.py`, `tests/evals/replay/`, plan de profesionalización 2026 (Fase 1),
  `docs/research/profundizacion-2026.md` (§1.2, §2.3)

## Contexto

Cerrada la Fase 0 (resiliencia + malla anti-injection en el canal público), la exploración de julio
2026 dejó cuatro gaps P1/P2 de agentes de cara a producción:

1. **Sin gobierno de gasto.** La medición (`ProveedorMedido` → `api_costo_diario`) **contabiliza**
   tokens pero no **corta**: un tenant (o un abuso) podía disparar el costo sin tope. No había
   rate-limit por empresa/usuario.
2. **Sin prompt caching.** El prefijo estable del prompt (catálogo de tools + system por tenant) se
   re-enviaba entero en cada llamada — costo y latencia evitables (Anthropic cachea el prefijo).
3. **Evals ciegas a la ruta LLM.** El replay solo cubría el bypass determinista (`--route llm` era un
   stub); los packs de WhatsApp (cotizaciones/cobranza/pedidos) y el handoff no se evaluaban.
4. **Sin métricas de agente** (handoff, fallback de proveedor, latencia p95, tokens/conversación) y el
   **writer de `memoria_entidades`** seguía diferido (el turno solo *leía* entidades, ver `ai/turno.py`).

## Decisión

### D1 — Compuertas atómicas en Redis ANTES de gastar la llamada al modelo (`core/llm/gobierno.py`)

Dos compuertas, cada una un **único `EVAL` (Lua)** → atómicas aun bajo `asyncio.gather`:

- **Rate-limit** por (tenant, usuario): ventana fija, clave `llm:rl:{tenant}:{usuario}` (INCR+EXPIRE,
  rechaza al superar el tope).
- **Presupuesto diario** por empresa: cada turno **RESERVA** un costo estimado contra un tope diario,
  clave `llm:budget:{tenant}:{fecha}` (fecha en **TZ Colombia**, regla #4). El script reserva-o-rechaza:
  si `usado + costo` no cabe, **rechaza sin tocar el contador** → el contador **jamás sobrepasa el
  tope**, ni bajo concurrencia.

`Gobierno.evaluar` corre rate-limit **antes** del presupuesto (un turno frenado por frecuencia no
consume presupuesto) y devuelve un `Decision`: al exceder, corta el turno con un **mensaje amable** al
usuario (nunca en silencio, nunca una excepción que escale a 500). Orden en los puntos de entrada: la
compuerta va **después del bypass** (determinista y barato, no paga el gate) y **después del
saneamiento** (WA), justo antes de resolver el proveedor y correr el bucle — un corte implica **0
llamadas al modelo** (invariante test-primero) y **no envenena la memoria**.

- **Defaults de plataforma** en `core/config/settings.py` (`gobierno_*`); **override por empresa** en
  `config_empresa` (`llm_rate_limite`, `llm_rate_ventana_s`, `llm_presupuesto_diario`), leído por el
  mismo `ConfigStore` del factory; **kill-switch en caliente** (`gobierno_habilitado`) — mismo patrón
  que la resiliencia F0. Los límites nacen en **0 = compuerta apagada** (opt-in): ningún tenant
  existente cambia hasta activarlos.
- **Fail-open:** un fallo de Redis se loguea y **NO** corta el turno — la compuerta es un guardrail, no
  correctitud (coherente con la medición best-effort). Se prefiere no bloquear el bot a bloquearlo por
  un blip de Redis.

**El presupuesto se mide en TOKENS estimados.** La reserva pre-llamada (`gobierno_costo_estimado_turno`)
acota el gasto de forma determinista; `Gobierno.registrar_uso` puede reconciliar con el uso real
(delta), pero por defecto la reserva estimada es el gate (ver *Desviaciones*).

### D2 — Prompt caching de Claude (`core/llm/providers/claude.py::_payload`)

Se marca `cache_control: {"type": "ephemeral"}` en el **prefijo estable**: la **última tool** del
catálogo (Anthropic cachea hasta el bloque marcado) y el **bloque system** (estable por tenant).
`traducir_tools` se mantiene **puro** (otros consumidores no cargan la marca); el breakpoint se agrega
solo al construir el payload. `ProveedorMedido` registra `cache_read_input_tokens` /
`cache_creation_input_tokens` (0/ausente en OpenAI) en el evento de métrica, **sin duplicar** el ledger
de costo (que sigue contando input/output).

### D3 — Ruta LLM de evals con corpus WA y LLM-as-judge opt-in (`tests/evals/replay/`)

`replay.py --route llm` queda **funcional**: un harness (`llm_route.py`) drivea el **bucle real** del
agente WA con un **ejecutor stub** (registra la tool pedida, devuelve un `Resultado` neutro), aislando
la **decisión del modelo** de la ejecución de dominio (ya cubierta por cada pack). Evalúa la elección de
herramienta por pack (cotizaciones/cobranza/pedidos) y el handoff (`corpus_wa.jsonl`). El **proveedor es
inyectado**: en tests, un fake scripteado (jamás API real); en corridas manuales, el proveedor real de
plataforma. El **LLM-as-judge** del texto libre es un puerto **opt-in** (`--judge`); el default
`JuezDesactivado` no evalúa (ni red ni costo).

### D4 — Métricas de agente + writer de `memoria_entidades`

- `ProveedorMedido` emite el evento estructurado `llm_uso` por llamada (proveedor, modelo, tokens,
  cache_read/creation, `latencia_ms`): superficie para derivar tokens/conversación y latencia p95. La
  **tasa de fallback** sale de `llm_fallback_proveedor` (ADR 0023) y la **de handoff** de los logs del
  pack handoff — todo `tenant_id`-scoped (regla #6).
- **Writer de `memoria_entidades` (cierra la deuda de `ai/turno.py`):** `RespuestaAgente` ahora
  transporta la `data` del `Resultado`; el handler del bot recuerda la última entidad —
  `consultar_producto → ultimo_producto`, `crear_cliente → ultimo_cliente`— vía `recordar_entidad`,
  **best-effort**. Mapeo conservador (solo tools con `data` inequívoca: `id` + `nombre`).

## Consecuencias

- (+) El gasto del agente queda **acotado por empresa** con un contador atómico que no sobregira; el
  abuso por frecuencia se frena por usuario. Todo opt-in y apagable en caliente.
- (+) El prompt caching recorta costo/latencia del prefijo estable sin tocar el bucle del agente.
- (+) La ruta LLM ya es evaluable (packs WA + handoff) sin gastar en APIs en CI; el juez de texto libre
  tiene su estructura lista para activarse fuera de CI.
- (+) El system prompt ya **consume y ALIMENTA** `memoria_entidades`: el bot recuerda el último
  cliente/producto entre turnos.
- (−) La compuerta agrega 1–2 `EVAL` a Redis por turno del modelo (no al bypass); mitigado por ser
  atómico y por el fail-open.
- (−) El presupuesto es **estimado** (reserva por turno), no el costo exacto medido post-hoc; ver abajo.

## Desviaciones del alcance (opción conservadora)

- **Presupuesto por estimación, sin columna nueva en control DB.** El override por empresa reusa
  `config_empresa` (texto plano), no una columna dedicada — cero migración, reversible. El gate es la
  **reserva estimada** por turno; la reconciliación con el uso real (`registrar_uso`) queda disponible
  pero **no cableada** al handler en esta fase (el ledger exacto sigue siendo `api_costo_diario`).
- **Juez real no cableado al CLI.** La *estructura* del LLM-as-judge existe (puerto `Juez`, flag
  `--judge`), pero el juez real (otro proveedor) se deja fuera de esta fase: los tests usan fakes y el
  CLI, sin key, cae al juez desactivado.
- **Métricas como logs estructurados,** no un backend de series de tiempo: se emite `llm_uso` con las
  dimensiones; la agregación (p95, tasas) vive en la capa de observabilidad (logs/Sentry), sin tabla
  nueva.
