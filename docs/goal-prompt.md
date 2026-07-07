# Prompt de arranque para `/goal` — Bot de ventas de Punto Rojo a producción

> Pega este bloque en `/goal` (Claude Code) y déjalo trabajar de forma autónoma. El contexto pesado vive en `docs/goal-bot-acierto-ventas.md`; este prompt es el norte + los guardarraíles + la Definición de Hecho.

---

## Misión (north star)
Lleva el bot de Telegram del tenant **Punto Rojo** en **ferrebot-saas** a estar **listo para producción**: lo más robusto posible, **sin alucinaciones**, que **acierte igual o más que `bot-ventas-ferreteria`**, y que se comporte como un agente de IA de primer nivel — **determinista donde se pueda, que pregunta cuando duda, que nunca inventa, idempotente, observable y probado**. Trabaja por fases hasta cumplir la Definición de Hecho; no te detengas antes salvo por los puntos de "PARAR y preguntar".

## Contexto (léelo ANTES de tocar código)
- **Plan, diagnóstico verificado, baseline medido y prioridades:** `docs/goal-bot-acierto-ventas.md` — EMPIEZA AQUÍ.
- **Rig de medición (eval de replay):** `tests/evals/replay/` (runner segmentado por fidelidad). **Extractor de datos reales** (catálogo + corpus desde la DB de producción del bot viejo en Railway): `bot-ventas-ferreteria/scripts/extraer_para_replay.py`.
- **Reglas del repo:** `CLAUDE.md` y `.claude/rules/` (multitenancy, testing, seguridad, performance, workflow).

## Punto de partida (medido, real — no empieces de cero)
En el corpus real de Punto Rojo el código YA está fuerte: **~90% en ventas fieles, 96% en enteros, 0 registros peligrosos** en lo representable. Los gaps son acotados y nombrados. Tu trabajo es **cerrar esos gaps**, no reescribir lo que ya funciona.

## Qué construir, en orden de impacto
1. **Unidad caja/ciento/gramo** (puntillas, lijas, tintes a granel). Hoy el bot hace `cantidad × precio` ("500 puntilla" → millones) y es el origen del **100% de los registros peligrosos**. Porta la semántica del bot viejo (`bot-ventas-ferreteria/bypass.py`, puntillas por gramos/pesos/caja) de forma **data-driven**, sin hardcode por tenant.
2. **Ruta LLM** para lo que el bypass difiere a propósito: **mayorista** y **multiproducto**. Cablear (ver `tests/test_bot_tenant_integration.py`) y medir.
3. **Capa de normalización/alias** (typos/abreviaciones): un normalizador universal en código + alias por tenant en datos. Medir con mensajes reales (`conversaciones_bot`) o un set curado.
4. **Resto de funciones a nivel producción:** factura electrónica DIAN (MATIAS, `city_id` interno, idempotente, async), inventario, gastos, caja, fiados — matriz de escenarios §5.5 del brief.
5. **Afinar** prompt/contexto del modelo y rieles donde el eval lo pida.

## Guardarraíles NO negociables (romper uno = bug crítico, bloquea merge)
- **ANTI-ALUCINACIÓN (prioridad #1 de calidad):** el bot **NUNCA** inventa precios, productos ni totales. Todo dato del negocio viene de **herramientas**, no del prompt (la SaaS es data-free a propósito). Producto desconocido/ambiguo → **pregunta** (riel R1). Total que no cuadra con el catálogo → **pregunta** (riel R2). Fracción sin precio → dilo y pregunta, no la calcules dividiendo. **Regla de oro en todo código nuevo: preferir preguntar/deferir antes que adivinar.**
- **Aislamiento multi-tenant**, **idempotencia**, y **"nada mueve stock/caja sin movimiento"** → **TEST-PRIMERO** (RED-GREEN-REFACTOR).
- Zona horaria Colombia (UTC-5), secretos cifrados (jamás en código/git/logs), acceso a datos **solo por repositorios**, `async/await` en endpoints con eventos, logging estructurado con `tenant_id`/`request_id`.

## Método (cómo operar autónomo)
- **Una fase a la vez:** implementar → correr `pytest` de la fase → correr el replay (`python -m tests.evals.replay.replay --corpus tests/evals/replay/corpus_puntorojo.jsonl --catalogo tests/evals/replay/catalogo_puntorojo.json`) → **comparar contra la línea base** → **NO avanzar si baja la paridad o aparece algún registro peligroso**.
- **Optimiza contra el corpus REAL** (`corpus_puntorojo.jsonl`), nunca contra `corpus_seed.jsonl` (es solo smoke test del rig). **No subas el número ablandando el corpus; súbelo tocando el bot.** Distingue fallo real de **deriva de precio** (ventas históricas vs catálogo de hoy) — esa deriva es ruido, no la persigas.
- Usa **subagentes** para revisión en paralelo (seguridad, performance, tipos) y los skills `engineering:*` según la tarea (`architecture` para ADRs, `code-review`, `debug`, `testing-strategy`).
- Commits `tipo: descripción` (sin atribución del asistente). PR con resumen y plan de prueba; CI en verde antes de mezclar.

## Definición de Hecho (cuándo terminaste)
- **Corpus real:** acierto en categorías fieles **≥ línea base y subiendo** hacia **≥97% en ventas simples**; `no_reconciliado` reducido al resolver unidad caja/ciento; `mayorista` medido por la ruta LLM y **≥ el bot viejo**.
- **Matriz de escenarios §5.5 en verde** (multiproducto, fracciones, mayorista, caja/ciento, wayper, factura DIAN, inventario, gastos, caja, fiados).
- **0 registros peligrosos** en lo fiel. Suite `pytest` **completa en verde**. Invariantes cubiertos por tests.
- Runbook de corte de webhook revisado (`docs/migracion-puntorojo.md`).

## PARAR y preguntarme (no decidas solo)
- Si necesitas el **corpus de mensajes reales** (typos/abreviaciones) más allá de los 45 actuales, o **claves de proveedor LLM** para medir la ruta LLM.
- Decisiones de **política con trade-off** (data-driven vs hardcode de precálculos; representación de cantidad mixta; umbrales de rieles).
- Cualquier **operación destructiva**, que mueva dinero, o que emita un documento fiscal real.

Trabaja hasta cumplir la Definición de Hecho. **Al cerrar cada fase reporta:** qué cambió, números del replay antes/después (por categoría), y qué sigue.
