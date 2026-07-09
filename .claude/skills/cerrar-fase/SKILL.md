---
name: cerrar-fase
description: Cierre de fase completo - suite backend troceada, evals, frontend (vitest+build), replay vs baseline, invariantes críticos y code review con corrección de CRITICAL/HIGH. Usar cuando el usuario diga "cierra la fase", "corre toda la suite", "gate de fase", o al terminar una fase del plan antes de mezclar.
---

# /cerrar-fase — el gate antes de mezclar

Regla del repo (`.claude/rules/development-workflow.md`): código libre dentro de la fase, TODA la suite al cierre. Ejecutar los pasos en orden; los lentos van en background. Reportar números REALES (passed/failed), nunca "parece que pasa".

## 1. Backend (en background, >10 min)

```bash
uv run pytest -ra --timeout=180 --timeout-method=thread --durations=15
```

- Correr en background (`run_in_background`) — NUNCA foreground con timeout de 2 min (gotcha redescubierto 3 veces).
- Mientras corre, avanzar con los pasos 2–4.
- Evals del agente (señal crítica, rápida — puede ir primero en foreground): `uv run pytest -m eval -ra --timeout=180 --timeout-method=thread`

## 2. Frontend

```bash
cd dashboard && npm run test && npm run build && npm run typecheck
cd landing && npm run test && npm run build     # solo si la fase tocó landing/
```

## 3. Replay eval vs baseline

```bash
.venv/Scripts/python.exe -m tests.evals.replay.replay \
  --corpus tests/evals/replay/corpus_seed.jsonl \
  --json-out <scratchpad>/replay_actual.json
```

Comparar contra `tests/evals/replay/baseline/baseline_seed.json`: debe seguir en 100% / 0 peligrosos. Si cambió, diff de `filas` para ver qué casos se movieron; una mejora legítima se commitea como baseline nuevo, una regresión se corrige.

## 4. Invariantes críticos (no negociables antes de mezclar)

Verificar que la fase los cubre y que pasan:

- **Aislamiento multi-tenant**: `tests/test_aislamiento_*.py` + `tests/evals/test_aislamiento.py` (va en `-m eval`).
- **Idempotencia**: `tests/test_compras_idempotency.py` + los tests con aserciones de idempotencia del área tocada (`grep -rln idempoten tests/`).
- **Nada mueve stock/caja sin movimiento**: si la fase tocó ventas/inventario/caja, señalar el test que lo cubre; si no existe, escribirlo AHORA (es el carve-out TDD del repo).

## 5. Code review

Correr `engineering:code-review` (o `/code-review`) sobre el diff de la fase. Corregir TODO lo CRITICAL y HIGH antes de seguir; lo MEDIUM/LOW se anota o se arregla si es barato.

## 6. Veredicto

Cuando termine el background del paso 1, consolidar: conteos reales de cada suite, replay vs baseline, invariantes, hallazgos de review corregidos. Si todo está en verde → listo para `/ship`. Si no, lista exacta de lo que falta.
