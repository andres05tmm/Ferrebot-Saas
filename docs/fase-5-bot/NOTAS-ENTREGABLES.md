# Fase 5 (bot) — notas de entregables (correcciones del checkpoint)

> Acordado en el checkpoint aprobado. Pendientes que NO son del entregable 1 pero deben
> cumplirse en su entregable. No borrar hasta cerrar la fase.

## Entregable 1 — transporte + webhook + tenant (EN CURSO)
- Handler dedicado `/tg/{slug}`, parseo propio del update (sin `python-telegram-bot`); un
  bot-token por empresa, sin `Application` viva por tenant (C1).
- **Orden no negociable:** resolver empresa → validar secret-token **en tiempo constante**
  (`hmac.compare_digest`) → dedup → sesión del tenant. Un secret inválido **nunca** abre la
  base del tenant.
- La **pre-validación sí lee el control DB** para descifrar el `webhook_secret` y el `bot_token`
  (plano de control, cifrados en `secretos_empresa`; claves `telegram_webhook_secret` y
  `telegram_token`). Esto es correcto y esperado (D3).
- **`usuarios.telegram_id`** (BIGINT UNIQUE) es el mapeo Telegram→usuario (schema.md), no
  `telegram_user_id`. Chat no mapeado / usuario inactivo → "no autorizado", sin mutar.
- **Dedup por `update_id` atado a la `idempotency_key`:** la key se deriva determinísticamente de
  `(tenant_id, update_id)` para que un reintento del webhook de Telegram reuse la misma key y el
  servicio de dominio dedup-ee aunque el dedup de Redis falle.
- **Pendiente de cobertura (composition root):** `ControlSecretosBot` (descifrado real desde
  `secretos_empresa`) y `ControlCapacidades` no tienen test directo todavía — los tests del
  entregable 1 inyectan fakes. Cuando se cablee el composition root del servicio bot (ensamblar
  `BotDeps` con repos reales + `tenant_session`), agregar un test de integración que: (a) siembre un
  secret cifrado con `core.crypto.encrypt_split` en el control DB y verifique que
  `ControlSecretosBot.webhook_secret` lo descifra (round-trip cripto), y (b) verifique
  `ControlCapacidades.efectivas` contra plan + `empresa_features`.

## Entregable 2 — bucle + NL híbrida (HECHO)
- Tope: **2 generaciones de modelo / 1 tool mutante** por turno. ✔ (`ai/agent.ejecutar_turno`)
- `Preguntar`/`Confirmar` van **directo** al usuario (sin ronda de modelo). Re-prompt **solo** para
  errores recuperables genuinos del servicio. ✔
- **CORRECCIÓN (producto_ambiguo):** `ItemResuelto.candidatos` migró `int → tuple[str, ...]`;
  `riel_producto` enumera los candidatos; `Dispatcher._rieles_venta` pasa `(prod.nombre,)`/`()`
  (conducta idéntica para venta por `producto_id`). ✔
- **Tripleta tool_use→tool_result:** `core.llm.base.Message` ganó `tool_calls`; OpenAI y Claude
  traducen el assistant-con-tool_call. El loop arma `user → assistant(tool_call) → tool` agnóstico.
- **Tripleta en providers cubierta** ✔: `test_llm_providers.py` valida `traducir_mensajes` para
  `user → assistant(tool_call) → tool` en OpenAI (arguments JSON string, `tool_calls[{id,type,function}]`)
  y Claude (bloques `tool_use` + `tool_result` emparejado por `tool_use_id`).
- **Pendiente de cobertura / wiring:**
  - **Token accounting:** `LLMResponse.usage` aún no se persiste en `api_costo_diario`. Cablear en
    el composition root del bot (necesita la sesión del tenant), no en el loop puro.
  - Wiring del loop como `TurnoHandler` del webhook (entregable 1 deja `procesar` inyectable):
    pendiente de ensamblar con `Dispatcher` real + `seleccionar_proveedor`. Al cablearlo:
    **(a)** `_MENSAJES_ERROR` ya da texto amable para `permiso_denegado`/`capacidad_no_habilitada`
    (no el `detail` técnico); mantener ese criterio para códigos nuevos. **(b)** el composition root
    DEBE envolver `ejecutar_turno` en `try/except` → mensaje de respaldo al usuario ante fallo del
    provider (timeout/credencial/5xx); nunca 500 ni silencio.

## Entregable 3 — convergencia del bypass por el despachador
- Bypass emite `ToolCall` normalizado → `dispatcher.ejecutar` (R1/R2 inertes por construcción,
  R3 + idempotencia vivos).
- **Test de paridad de convergencia debe cubrir explícitamente fracciones y venta por peso/caja**
  (no solo enteros): ahí es donde normalizar a `ToolCall` puede perder fidelidad vs FerreBot.
- **Resolver la doble lectura del producto en el camino caliente (60%):** al converger, R1 vuelve a
  leer el producto que el bypass ya resolvió con `producto_exacto`. Opciones (elegir y dejar
  explícito, no silencioso): (a) `catalogo.obtener` respaldado por el `price_cache` de Redis
  (lectura por PK cacheada), o (b) el bypass pasa el `ProductoCatalogo` ya resuelto dentro de
  `Recursos` para que R1 no relea Postgres.

## Entregable 4 — contexto RAG del turno
- Tablas que SÍ existen (schema.md): `conversaciones_bot`, `memoria_entidades`,
  `ventas_pendientes_voz`, `audio_logs`.
- **`memoria_turno` y `price_cache` NO existen como tablas** en schema.md → `price_cache` va a
  **Redis** (caché, fuera del prompt); `memoria_turno` se modela como scratch del turno
  (Redis/efímero), no tabla de negocio. Confirmar al planear el entregable.
- **Token accounting por wrapper, NO por `RespuestaAgente.usage`** (decisión del checkpoint E4):
  `ai.agent` tiene 6 puntos de salida + camino de 2 generaciones; contar ahí sub-cuenta la ruta de
  tool y obliga a sumar a mano. Se cuenta en `core.llm.medicion.ProveedorMedido`, que envuelve el
  `LLMProvider` y acumula `response.usage` (best-effort) en un `CostosStore`
  (`modules.memoria.SqlCostosRepository` → `api_costo_diario`, fecha Colombia, PK=fecha, upsert
  acumulativo, modelo = último escritor). El `TurnoHandler` solo cablea el wrapper.
- **FOLLOW-UP — writer de `memoria_entidades` (DIFERIDO, no es de E4):** recordar último
  cliente/producto al final del turno necesita la fuente correcta = `Resultado.data`, que el handler
  no ve hoy (`RespuestaAgente` no transporta `data`). Pendientes del mini-spec: (a) exponer/llevar
  `Resultado.data` hasta el orquestador (¿campo nuevo en `RespuestaAgente`? ¿otra superficie?);
  (b) estandarizar el mapeo tool→entidad (qué tool produce `ultimo_cliente`/`ultimo_producto` y de
  qué llaves de `data` salen `id`/`nombre`). En E4 el system prompt solo CONSUME `leer_entidades`
  (lectura, degrada a sin "Contexto reciente" si vacío); `recordar_entidad`/`leer_entidades` ya están
  probados a nivel de servicio (round-trip + upsert + aislamiento por chat).

## Entregable 5 — voz
- `Transcriptor` como puerto aparte (no dentro de `LLMProvider`). Reusar R3/Redis para confirmación
  de voz; `ventas_pendientes_voz` como auditoría, no máquina de estado paralela.

## Observabilidad (transversal, cierra deuda del despachador)
- `request_id` (update_id) + `tenant_id` en los contextvars de `core.logging` al inicio de cada
  update; eventos estructurados de turno (ruta, intent, latencia, fallback, tool, riel, error);
  tokens a `api_costo_diario`. Nunca `print`.
