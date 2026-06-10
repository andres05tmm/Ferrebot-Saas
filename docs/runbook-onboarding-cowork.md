# Runbook — Onboarding mágico v1 en Cowork (costo API $0)

> 9 jun 2026 · ADR 0011 §D4. El alta v1 es ASISTIDA: la extracción la hace **Cowork dentro de la
> sesión** (visión cubierta por la suscripción — sin API key, sin worker, sin gasto marginal). Este
> runbook es el contrato operativo entre Andrés y Cowork. Prerequisito: pack POS del manifiesto
> (plan F1) y el flag `--check` (plan F1b) mezclados.

## Flujo (10-15 min por alta)

1. **Andrés entrega los insumos** en la sesión de Cowork: fotos de la lista de precios (legibles,
   una sección por foto), screenshots de Instagram, y/o el Excel/CSV del catálogo. También: slug,
   NIT, nombre, email del admin, packs/plan, y `phone_number_id` de Kapso si ya existe.
2. **Cowork extrae** aplicando el contrato anti-alucinación (abajo): map por insumo → normalización
   → enriquecimiento (categorías, fracciones, aliases, escalonados) → ensamblado del manifiesto.
   El Excel lo procesa con código (pandas/openpyxl en su sandbox), no "a ojo".
3. **Cowork escribe** `tools/onboarding/<slug>.yaml` (gitignored; los secretos nunca a git) y
   **valida** con el validador real:
   `python -m tools.provision_from_manifest --from tools/onboarding/<slug>.yaml --check`
4. **Cowork presenta a Andrés SOLO lo que necesita ojos** (no las 600 filas): dudas (ilegible,
   ambiguo), outliers de precio por categoría, inferencias hechas (fracciones/aliases/categorías
   marcadas como inferidas), cobertura (filas vistas vs extraídas) y un resumen estadístico.
5. **Andrés corrige/confirma** → Cowork actualiza el YAML y re-valida hasta `VALIDO`.
6. **Aplicar**: por el panel `/admin` (crear tenant pegando el manifiesto) o
   `railway ssh` + `python -m tools.provision_from_manifest --from ...` (gotcha del handoff:
   provisioning en prod = EN-RED).
7. **Smoke**: el resumen de una línea del provisionador + 2-3 preguntas de prueba al agente
   (incluyendo una venta con fracción) antes de entregar al cliente.

## Contrato anti-alucinación (Cowork DEBE cumplirlo — ADR 0011 §D6)

- **Transcribir, jamás inventar.** Lo ilegible o ausente → campo `null` + entrada en la lista de
  dudas con referencia a la foto. Prohibido "completar" precios, unidades o nombres plausibles.
- **Toda inferencia se declara.** Categorías, `permite_fraccion`, fracciones típicas del gremio,
  aliases y escalonados que no estén literales en el insumo se presentan como inferencias a
  aprobar, no como hechos.
- **Outliers obligatorios.** Precio > ~5x la mediana de su categoría → duda, aunque "se lea claro"
  (la lija de $400k). Verificación aritmética de fracciones: decimal × precio_unitario ≈
  precio_total.
- **Cobertura medida.** Reportar cuántas filas se ven en cada foto vs cuántas se extrajeron; si la
  foto está borrosa, pedir otra foto en vez de adivinar.
- **El validador manda.** Nada se da por bueno hasta que `--check` imprima `VALIDO`; los errores
  del `ErrorManifiesto` se corrigen contra el insumo, no "a criterio".
- **Secretos**: si el manifiesto lleva secretos (MATIAS, etc.), solo en el YAML gitignored; jamás
  en el chat más de lo necesario, jamás en logs.

## Cuándo graduar a v2 (pipeline API con router — plan F2-F4)

Cuando haya altas SIN Andrés presente (self-serve) o >2-3 altas/semana. El presupuesto de v2 es
≤ USD $1-2 por alta grande (map en Sonnet, Fable solo letra dura y reduce). El eval dorado
(`tools/eval_extractor.py`, plan F5) audita ambos mundos con los mismos umbrales.
