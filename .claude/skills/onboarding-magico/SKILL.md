---
name: onboarding-magico
description: Onboardear un cliente de FerreBot SaaS desde insumos naturales (fotos de listas de precios, screenshots de Instagram, Excel/CSV del catálogo) hasta un manifiesto YAML válido listo para provisionar. Usar cuando el usuario diga "onboardear/dar de alta un cliente", "extrae esta lista de precios", "arma el manifiesto", o entregue fotos/Excel de un catálogo de negocio. Aplica el contrato anti-alucinación del ADR 0011 de ferrebot-saas.
---

# Onboarding mágico — insumo natural → manifiesto válido (ADR 0011 v1)

Convierte lo que el dueño del negocio tiene (fotos, screenshots, Excel) en un manifiesto de tenant
válido que `tools/provision_from_manifest.py` consume. El modelo TRANSCRIBE y estructura; los
scripts y el validador del repo deciden. Referencias del repo: `docs/adr/0011-*.md`,
`docs/runbook-onboarding-cowork.md`, esquema en `tools/manifest/schema.py`, ejemplo en
`tools/onboarding/ferreteria-demo.manifest.example.yaml`.

## Contrato anti-alucinación (NO negociable)

1. **Transcribir, jamás inventar.** Campo ilegible o ausente → `null` + entrada en DUDAS con
   referencia al insumo (foto #, fila). Prohibido completar precios/nombres/unidades "plausibles".
2. **Toda inferencia se declara.** Categorías, `permite_fraccion`, fracciones típicas del gremio,
   aliases y escalonados que no estén literales en el insumo → lista de INFERENCIAS a aprobar.
3. **Cobertura medida.** Por cada insumo: cuántas filas se VEN vs cuántas se extrajeron. Foto
   borrosa → pedir otra foto, no adivinar.
4. **El validador manda.** Nada está "bien" hasta que `--check` imprima `VALIDO`. Los errores se
   corrigen contra el insumo, no a criterio.
5. **Secretos solo en el YAML gitignored** (`tools/onboarding/`), jamás en logs ni en el chat más
   de lo necesario.

## Procedimiento

### 1. Reunir lo que una foto no trae
Pedir al operador (si no lo dio): `slug` (minúsculas, `[a-z0-9-]`), `nombre`, `nit`, email del
admin, plan/packs a activar, `phone_number_id` de Kapso si existe. Sin esto el manifiesto no valida.

### 2. MAP — transcribir cada insumo a filas crudas
Por cada foto/screenshot: transcribir a filas `{nombre_visto, precios_vistos[], unidad_vista,
notas, origen}`. Excel/CSV: NO leer "a ojo" — convertir con código (pandas/openpyxl) a CSV crudo.
Reportar cobertura por insumo (regla 3).

### 3. NORMALIZE — código, no criterio
Correr `scripts/normalizar_precios.py` sobre las filas crudas (CSV/JSON):
```
python scripts/normalizar_precios.py filas.csv > normalizado.json
```
Hace: parseo de precios colombianos ("12.500", "$ 12,5k", "12.500/m"), normalización de nombres,
detección de duplicados y **outliers por categoría** (>5x mediana → DUDA obligatoria, aunque "se
lea claro"). Sus flags van directo a la lista de DUDAS.

### 4. REDUCE — enriquecer marcando todo
Sobre las filas normalizadas: asignar `categoria`, `permite_fraccion` + `fracciones[]`, `aliases`
de typos/regionalismos, `escalonado` si la lista lo sugiere ("docena a..."). TODO esto es
INFERENCIA (regla 2) salvo que esté literal en el insumo. Verificación aritmética: si una fracción
trae `decimal` y `precio_unitario`, debe cumplir `decimal × precio_unitario ≈ precio_total` (±1
peso) — si no cuadra, DUDA, no "corregir".

### 5. Ensamblar el YAML
Estructura según `tools/manifest/schema.py` (secciones: `identidad`, `admin`, `plan`,
`features_override`, `branding`, `packs.pos|agenda|faq`, `canal`). Copiar la forma del ejemplo
`ferreteria-demo.manifest.example.yaml`. Guardar en `tools/onboarding/<slug>.yaml` (gitignored).
Negocios de servicios (clínica/spa): mismo flujo con `packs.agenda` (servicios/recursos/
disponibilidad) y `packs.faq` en vez de `packs.pos`.

### 6. Validar con el validador REAL
```
python -m tools.provision_from_manifest --from tools/onboarding/<slug>.yaml --check
```
`VALIDO` → seguir. `INVALIDO` → corregir contra el insumo y repetir. Máximo necesario; nunca
saltarse este paso.

### 7. Presentar SOLO lo que necesita ojos humanos
NO mostrar las 600 filas. Mostrar: tabla de DUDAS (dato + insumo de origen), OUTLIERS, lista de
INFERENCIAS (aceptar/rechazar en lote), cobertura por insumo, y resumen estadístico (n productos,
rango de precios por categoría). El operador corrige/confirma → actualizar YAML → re-validar.

### 8. Entregar
El YAML validado se aplica por el panel `/admin` (crear tenant) o en prod vía
`railway ssh` + `python -m tools.provision_from_manifest --from ...`. El skill NUNCA aprovisiona
por su cuenta: la confirmación es del operador. Smoke final: 2-3 preguntas al agente, incluida una
venta con fracción.

## Si el modelo de la sesión es liviano (Sonnet/Haiku)

Excel/CSV e impresos nítidos: proceder normal. Letra a mano difícil: intentar; si la cobertura
queda <80% o las dudas >20% de las filas, decirlo explícitamente y recomendar (a) mejores fotos o
(b) repetir el MAP en una sesión con modelo más capaz. Nunca compensar baja legibilidad con
imaginación — el contrato (regla 1) aplica igual con cualquier modelo.
