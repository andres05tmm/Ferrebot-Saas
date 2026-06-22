# Harness de replay — medir el acierto del bot de ventas

Mide qué tan bien el bot de **ferrebot-saas** registra ventas, pasando un corpus de mensajes por el
runtime REAL del agente y comparando el `ToolCall` emitido con el resultado esperado. Es el motor de
medición de paridad descrito en `docs/goal-bot-acierto-ventas.md` (§5, Fase 0).

## Qué hay aquí

| Archivo | Para qué |
|---|---|
| `replay.py` | Runner: corre el corpus, captura la venta emitida, reporta acierto por categoría + global. |
| `corpus_seed.jsonl` | Corpus sintético (22 casos) sobre el catálogo del harness. **Corre hoy, sin BD ni LLM.** |
| `extraer_catalogo.py` | Exporta `productos`+`fracciones` de un tenant Postgres → `catalogo.json` (para replay con datos reales). |
| `extraer_corpus.py` | Extrae corpus real de la DB del bot viejo (reconstruido de `ventas_detalle` o crudo de `conversaciones_bot`). |

## Arranque rápido (hoy, sin datos reales)

Desde la raíz del repo, con el entorno del proyecto (deps de `pyproject.toml`, Python 3.12):

```bash
python -m tests.evals.replay.replay --corpus tests/evals/replay/corpus_seed.jsonl
```

Esto corre el camino **bypass** (determinista, sin BD ni clave de proveedor) y debe dar **100% de
acierto y 0 peligrosos** sobre el corpus semilla. Si algo falla, es una regresión real del bypass.

> El corpus semilla usa el catálogo de `tests/evals/_harness.py` (vinilo id7, manguera id8,
> puntilla id9, tornillos caja id10, cemento id11 escalonado, lija id12, thinner id13, drywall id14).

## Con datos reales de Punto Rojo

1. **Sembrar la base** del tenant con el dump real (ver `docs/goal-bot-acierto-ventas.md` §5.2):
   ```bash
   python -m tools.provision_from_manifest --from tools/onboarding/puntorojo.json
   pg_restore --clean --if-exists -d ferrebot_puntorojo backups/<más-reciente>/ferrebot_puntorojo.dump
   ```
2. **Exportar el catálogo real** a JSON:
   ```bash
   python -m tests.evals.replay.extraer_catalogo \
       --db-url "postgresql://user:pass@host:5432/ferrebot_puntorojo" --out catalogo_puntorojo.json
   ```
3. **Obtener el corpus real** (la DB de producción del bot viejo vive en Railway, no en el repo):
   ```bash
   # ventas reconstruidas (etiquetado automático, ideal para el bypass):
   python -m tests.evals.replay.extraer_corpus --db-url "$PROD_DATABASE_URL" \
       --modo ventas --out corpus_puntorojo.jsonl
   # mensajes literales (para la ruta LLM / etiquetar a mano):
   python -m tests.evals.replay.extraer_corpus --db-url "$PROD_DATABASE_URL" \
       --modo conversaciones --out corpus_mensajes.jsonl
   ```
4. **Correr el replay** contra el catálogo real:
   ```bash
   python -m tests.evals.replay.replay \
       --corpus corpus_puntorojo.jsonl --catalogo catalogo_puntorojo.json \
       --umbral 0.97 --json-out reporte_saas.json
   ```

## Cómo se lee el resultado

- **acierto**: `ok / n`. `ok` = la herramienta correcta con producto, cantidad y total correctos
  (total con tolerancia 1% o $1, la misma del riel de precio).
- **cobertura bypass**: cuántas resolvió el camino rápido vs. cuántas difirió al modelo (deferir NO es
  un fallo: el modelo las tomaría; mide cuánto cubre el bypass).
- **peligrosos** ⚠: registró una venta **mala** (`fail_items`) o **indebida** (`fail_registro_indebido`,
  registró cuando debía preguntar/deferir). Cualquier peligroso > 0 hace fallar el veredicto.
- **código de salida**: `0` si acierto ≥ umbral y 0 peligrosos; `1` si no; `2` error de uso.

## Paridad vs. el bot viejo

Corre el **mismo corpus** por ambos bots y compara los `--json-out`. El bot viejo
(`bot-ventas-ferreteria`) expone su bypass como `bypass.intentar_bypass_python(mensaje, catalogo)`
(otra firma, otro repo): hace falta un pequeño adaptador que envuelva esa función y emita el mismo
`reporte.json`. La meta de `docs/goal-bot-acierto-ventas.md` es acierto(saas) ≥ acierto(viejo) en cada
categoría. *(El adaptador del bot viejo es el siguiente entregable; pídelo cuando tengamos el corpus.)*

## Ruta LLM (pendiente — Fase 0 LLM)

`replay.py` cubre hoy el camino **bypass**. El 40% de mensajes que caen al modelo (multiproducto,
escalonado, gastos/fiados/consultas en lenguaje natural) necesitan:

- un **tenant sembrado** (base real) y una **clave de proveedor** configurada,
- disparar el turno completo con `apps/bot/webhook.py:manejar_update` o el loop de `ai/agent.py`,
  capturando la respuesta con un `FakeNotificador` y leyendo `conversaciones_bot`.

El patrón está en `tests/test_bot_tenant_integration.py`. `--route llm` queda reservado para ese
cableado (hoy avisa y sale). Es trabajo de la Fase 0 del brief, una vez haya datos + clave.

## Notas

- Las dependencias son las de `pyproject.toml` (el `.venv` del repo ya las tiene). Python 3.12.
- El runner construye un **harness nuevo por caso** (sin fuga de estado) y usa una `idempotency_key`
  única por caso.
- Las consultas SQL de los extractores siguen `modules/inventario/models.py`; para el bot viejo,
  **verificar nombres de columna** contra sus migraciones (`003_migrate_ventas.py`,
  `018_conversaciones_bot.py`).
