# ADR 0023 — Resiliencia de la capa LLM y saneamiento en el canal público

- **Estado:** Aceptado
- **Fecha:** 2026-07-02
- **Relacionados:** ADR 0005 (LLM por empresa), ADR 0015/0016/0017 (packs WA), `ai/saneamiento.py`, plan de profesionalización 2026 (Fase 0)

## Contexto

Dos gaps P0 de seguridad/confiabilidad detectados en la exploración de julio 2026:

1. **Sin reintentos en la capa LLM.** `ClaudeProvider.generate` / `OpenAIProvider.generate` hacían
   **una sola** llamada al SDK. Un 429, 5xx o timeout transitorio tumbaba el turno directo al
   mensaje de respaldo (`MENSAJE_RESPALDO` / `FALLBACK`), aunque un reintento a los 500 ms habría
   respondido. La factory ya era multi-proveedor (`providers/openai.py` existe) pero sin fallback
   cableado.
2. **El canal público sin malla anti-injection.** `ai/saneamiento.revisar` (texto desmesurado,
   caracteres de control, inyección de instrucciones, números absurdos) corría SOLO en
   `ai/dispatcher.py` — la ruta del bot interno de Telegram (vendedores de confianza). El agente de
   WhatsApp (`apps/wa/agent.py::ejecutar_runtime`), el canal expuesto a **usuarios no confiables**,
   despachaba las tools con la sola validación Pydantic, y el texto entrante del cliente llegaba al
   modelo sin revisión. Era exactamente el disparador que `plan-mejoras-2026` (Fase 0, #16b) fijó
   para endurecer: "lanzamiento del canal WhatsApp".

## Decisión

### D1 — Excepciones canónicas + retry en el borde del proveedor, como decorador

- `core/llm/base.py` gana `LLMTransitorio` (429/5xx/timeout/conexión → reintentable) y
  `LLMPermanente` (4xx de petición/auth → no reintentar). Los clientes reales de cada proveedor
  traducen el error crudo del SDK con `clasificar_excepcion` (duck-typing sobre `status_code` y
  nombre de clase: no se importan los SDKs en la capa de resiliencia). Lo desconocido se propaga
  intacto: **ante la duda, no reintentar**.
- `core/llm/resiliencia.py::ProveedorResiliente` decora un `LLMProvider` (mismo patrón que
  `ProveedorMedido`): reintenta SOLO `LLMTransitorio`, backoff exponencial con jitter
  (base 0.5 s × 2^intento, tope 8 s, 3 intentos), `sleep`/`rng` inyectables para tests.
- **Invariante (test-primero):** el reintento vive en el borde del `generate`; el bucle del agente
  no cambia → un reintento **jamás re-ejecuta herramientas ya despachadas**
  (`tests/test_llm_resiliencia.py`).

### D2 — Fallback de proveedor: una vez, con SUS modelos, opt-in por .env

`get_llm_con_fallback` (en `core/llm/factory.py`) envuelve el proveedor resuelto. Si
`llm_fallback_provider` apunta a OTRO proveedor con key disponible, al agotar los reintentos del
primario cae **una sola vez** al respaldo, usando `llm_fallback_model_worker/orquestador` (los
nombres de modelo no cruzan entre vendors). Un `LLMPermanente` NO activa el respaldo (fallaría
igual). `llm_retry_habilitado=false` es el kill-switch en caliente: devuelve el proveedor pelado.
Consumidores: `Dispatcher.seleccionar_proveedor` (bot) y el `resolver_llm` del worker (WA). El
orden de decoradores queda `ProveedorMedido(ProveedorResiliente(provider))`: el costo se cuenta
sobre la respuesta que efectivamente volvió.

### D3 — La malla de saneamiento corre en TODA ruta que ejecute tools, y sobre el texto entrante

- `apps/wa/agent.py::ejecutar_runtime` pasa `ai.saneamiento.revisar` sobre los args crudos ANTES de
  despachar a cualquier pack. Bloqueado → `ErrorTool("validacion", …)` (no recuperable en
  inyección: no se invita al modelo a "reescribir" el ataque) + log `wa_saneamiento_bloqueo`.
- `AgenteWa.atender` revisa el **texto entrante** del cliente (la inyección llega por el mensaje,
  no solo por args): bloqueado → respuesta fija `RECHAZO_ENTRADA` **sin invocar el LLM**, sin
  escribir la memoria (no se envenena el historial), y con el hilo del inbox completo (entrante +
  saliente) para visibilidad del negocio. El chequeo corre después de la pausa por handoff (una
  conversación en manos de un humano no recibe respuestas del bot).
- **Invariante (test-primero):** una entrada con inyección jamás ejecuta una herramienta en WA
  (`tests/test_wa_agent_saneamiento.py`).

## Consecuencias

- (+) Un blip transitorio del proveedor ya no degrada el turno; con respaldo configurado, ni un
  outage del vendor primario.
- (+) El canal público queda al mismo nivel de defensa que el interno (malla previa + Pydantic +
  rieles + límites), más el gate del texto entrante que el interno no necesita.
- (−) Cada reintento paga latencia (hasta ~3×timeout en el peor caso) y tokens si el fallo es
  post-facturación; mitigado por el tope de intentos y el kill-switch.
- (−) El guardrail sigue siendo heurístico (regex): el clasificador dedicado en instancia separada
  (plan Fase 1, #16b completo) queda pendiente; esta malla ataja lo evidente.
- Los reintentos se distinguen en logs (`llm_reintento`, `llm_fallback_proveedor`) para no
  confundir el costo diario.
