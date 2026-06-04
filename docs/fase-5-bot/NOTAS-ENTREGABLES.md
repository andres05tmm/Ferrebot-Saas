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

## Entregable 2 — bucle + NL híbrida
- Tope: **2 generaciones de modelo / 1 tool mutante** por turno.
- `Preguntar`/`Confirmar` van **directo** al usuario (sin ronda de modelo). Re-prompt **solo** para
  errores recuperables genuinos del servicio.
- **CORRECCIÓN (producto_ambiguo):** hoy `ItemResuelto` solo lleva el conteo de candidatos; antes
  de mandar el corte directo, el riel/despachador debe **incluir la lista de nombres de candidatos**
  (pasarlos a `riel_producto` o componer la lista en el despachador). Nada de "¿cuál?" sin mostrar
  cuáles.

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

## Entregable 5 — voz
- `Transcriptor` como puerto aparte (no dentro de `LLMProvider`). Reusar R3/Redis para confirmación
  de voz; `ventas_pendientes_voz` como auditoría, no máquina de estado paralela.

## Observabilidad (transversal, cierra deuda del despachador)
- `request_id` (update_id) + `tenant_id` en los contextvars de `core.logging` al inicio de cada
  update; eventos estructurados de turno (ruta, intent, latencia, fallback, tool, riel, error);
  tokens a `api_costo_diario`. Nunca `print`.
