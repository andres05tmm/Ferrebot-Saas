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
- **Implementado en E5:** `core.voz.transcriptor` (puerto `Transcriptor` + `Transcripcion` +
  esqueleto `WhisperTranscriptor`); `core.voz.filtros.es_transcripcion_silencio` (porta las REGLAS
  de `ai/voz_filtros.py`: alucinaciones conocidas + `no_speech_prob` alto); puerto
  `apps.bot.ports.ArchivosTelegram`; `SqlAudioLogsRepository` (savepoint, misma disciplina
  best-effort); el `TurnoHandler` enchufa voz ANTES del pipeline (capacidad `ventas_voz` →
  descargar → transcribir → filtrar silencio → mismo flujo con el texto transcrito, que también es
  el que se persiste como mensaje del usuario).
- **FOLLOW-UP — `ventas_pendientes_voz` (auditoría, DIFERIDO):** la confirmación de voz reusa
  R3/Redis a través del mismo pipeline; la fila de auditoría necesita una **superficie limpia de la
  señal "confirmación pendiente"** (hoy el handler no la ve) → pendiente de mini-spec. No es máquina
  de estado paralela.
- **FOLLOW-UP — prompt adaptativo de Whisper + corrección Haiku (DIFERIDO):** minar vocabulario del
  catálogo / `audio_logs` para el `prompt` de Whisper y una pasada de corrección con Haiku son
  optimizaciones. En E5 el `prompt` de Whisper va plano (`None`). `limpiar_texto_voz` NO se porta
  (solo aplica con TTS).

## CR-1 — adaptadores externos httpx (Telegram / Whisper)
- `apps.bot.telegram` (`TelegramNotificador`, `TelegramArchivos`) y `core.voz.transcriptor`
  (`WhisperTranscriptor`) aíslan el HTTP tras un cliente inyectable (patrón de
  `core/llm/providers/openai.py`): impl real perezosa en `_cliente_telegram`/`_cliente_whisper`
  (httpx importado dentro, nunca al cargar el módulo). El enlace token↔empresa es CR-3.
- **FOLLOW-UP — chunking de mensajes > 4096:** `TelegramNotificador.responder` hace UN solo
  `sendMessage`; la Bot API corta en 4096 chars. Partir mensajes largos en varios envíos → pendiente.
- **FOLLOW-UP — idioma de Whisper hardcoded `"es"`:** `WhisperTranscriptor._payload` fija
  `language="es"`; hacerlo per-empresa (config) cuando entren tenants de otra región → pendiente.
- **Asunción — filename `audio.ogg` en el multipart de Whisper:** `_cliente_whisper` manda el audio
  como `("audio.ogg", audio, "audio/ogg")` porque la voz de Telegram es OGG/Opus y OpenAI infiere el
  formato por la extensión. Si entra otra fuente de audio (no Telegram), revisar la extensión/MIME.

## CR-3 — binding de recursos por empresa + composition root (HECHO)
- **CR-3a — `RecursosBot` (binding por empresa):** `apps.bot.recursos.RecursosBot` cachea por
  `empresa_id` (espejo de `core.db.engine_cache.EngineCache`: lock global mantenido a través de la
  carga) un `RecursosEmpresa(notificador, transcriptor, archivos)` ya atado al bot-token / api-key de
  esa empresa. El webhook pide `deps.recursos.para(tenant.id)` tras validar el secret y usa
  `bundle.notificador`; el `TurnoHandler` resuelve el bundle con `recursos.para(ctx.tenant_id)` en la
  rama de voz. `crear_turno_handler` ya no recibe `transcriptor`/`archivos` sueltos (param `recursos`).
- **CR-3b — composition root (`apps.bot.wiring.construir_deps` + `apps.bot.main`):** ensambla
  `BotDeps` desde los puertos reales con **sesión de control PER-CALL** (cada wrapper —`ResolverControl`,
  `SecretosControl`, `CapacidadesControl`, `ConfigControl`, `KeyControl`— abre una `AsyncSession` de
  control FRESCA por llamada y delega en las clases existentes). `core.db.session.control_session`
  (nuevo, espejo de `tenant_session`). `app = crear_app()` a nivel de módulo; `main()` con
  `uvicorn.run("apps.bot.main:app", host=$HOST, port=$PORT)` (PORT lo inyecta Railway). La
  construcción NO hace I/O: todo difiere a las llamadas (seams inyectables para el smoke sin red).
- **Cobertura cerrada (lo que E1 dejó pendiente):** `tests/test_bot_repos_control.py` hace el
  round-trip cripto de `ControlSecretosBot` (webhook secret + bot token) y `ControlCapacidades`
  (plan ± `empresa_features`) contra un control DB efímero; `tests/test_bot_wiring.py` valida el
  cableado de todos los puertos, el camino "no autorizado" extremo a extremo SIN red, y que el
  `_cargar` real del `RecursosBot` descifra `telegram_token` + `openai_api_key`.
- **Token accounting (lo que E2/E4 dejó pendiente) — cableado:** `procesar` se arma con
  `crear_turno_handler(..., recursos=…, confirm=RedisConfirmStore, turno=WORKER)`; el wrapper
  `ProveedorMedido` acumula `response.usage` en `SqlCostosRepository` por la sesión del tenant.

## Deuda viva tras el cierre de Fase 5 (explícita — NO perder)
- **`audio_logs` de voz NO se registra (`audios=None`):** `SqlAudioLogsRepository` existe (E5) pero el
  composition root pasa `audios=None` a `crear_turno_handler` → la bitácora de transcripciones de voz
  queda en CERO (la voz funciona; solo se omite el log de auditoría). Cierre: cablear la factory
  `audios = lambda s: SqlAudioLogsRepository(s)` en `construir_deps` cuando se quiera la auditoría.
- **Writer de `memoria_entidades` (último cliente/producto) sin implementar:** el turno solo CONSUME
  `leer_entidades` (lectura); nadie escribe `recordar_entidad` al cerrar el turno. Bloqueado por el
  mini-spec de E4 (ver FOLLOW-UP de E4): la fuente correcta es `Resultado.data`, que `RespuestaAgente`
  no transporta hoy. Hasta entonces el bloque "Contexto reciente" del system prompt nace vacío.

## Observabilidad (transversal, cierra deuda del despachador)
- `request_id` (update_id) + `tenant_id` en los contextvars de `core.logging` al inicio de cada
  update; eventos estructurados de turno (ruta, intent, latencia, fallback, tool, riel, error);
  tokens a `api_costo_diario`. Nunca `print`.
