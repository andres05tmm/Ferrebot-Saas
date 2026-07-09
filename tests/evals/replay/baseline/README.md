# Baselines de replay eval (persistentes)

Baselines versionados del replay eval, para comparar entre sesiones sin depender de
archivos en `/tmp` (que se pierden al cerrar la sesión o apagar el PC).

## Archivos

| Archivo | Corpus | Cómo regenerar |
|---|---|---|
| `baseline_seed.json` | `corpus_seed.jsonl` (22 casos, sin BD ni LLM) | comando de abajo |
| `baseline_puntorojo.json` | corpus real de Punto Rojo (requiere catálogo + corpus extraídos de prod) | ver sección Punto Rojo |

## Regenerar el baseline semilla

```bash
python -m tests.evals.replay.replay \
  --corpus tests/evals/replay/corpus_seed.jsonl \
  --json-out tests/evals/replay/baseline/baseline_seed.json
```

Debe dar **100% de acierto y 0 peligrosos**. Si baja, es una regresión del bypass:
comparar `filas` contra el baseline commiteado para ver qué casos cambiaron.

## Regenerar el baseline de Punto Rojo

Requiere el catálogo y el corpus extraídos (ver `tests/evals/replay/README.md` §datos reales):

```bash
python -m tests.evals.replay.replay \
  --corpus corpus_puntorojo.jsonl \
  --catalogo catalogo_puntorojo.json \
  --umbral 0.97 \
  --json-out tests/evals/replay/baseline/baseline_puntorojo.json
```

Commitear el baseline actualizado junto con el cambio que justifica la diferencia.
El JSON es determinista (conteos y filas, sin timestamps), así que el diff de git
muestra exactamente qué casos cambiaron de veredicto.
