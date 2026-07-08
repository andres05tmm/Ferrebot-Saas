# GOAL (seguimiento) — Mejoras de dominio: lija esmeril (por cm) + vinilo por tipo

> Dos bugs reales detectados en el smoke test de producción (bot ya en vivo, PR #60). Implementar con la misma disciplina del brief principal (`docs/goal-bot-acierto-ventas.md`): por fases, midiendo con el replay (`tests/evals/replay/`), sin romper lo que ya funciona (enteros 97%, fracciones, mayorista, anti-alucinación, 0 peligrosos). Respetar invariantes (multitenancy, idempotencia, TZ Colombia, repos-only).

---

## Bug 1 — Lija: "normal" (por hoja) vs "esmeril" (por centímetro)

Son **dos productos distintos** y el bot los confunde:

- **Lija normal** — `"lija N°X"` SIN la palabra "esmeril" → se vende **por hoja/unidad**. Precio por unidad (ej. `Lija N°60` ≈ $2.000). En catálogo ya existe aparte (`Lija N°60`, `Lija N°600`, etc.) y funciona ("1 lija" → $2.000 OK).
- **Lija esmeril** — `"lija esmeril N°X"` → se vende **por centímetro**, el cliente pide los cm que quiera → **hay que calcular**.

### Reglas a implementar
1. **Distinguir el producto por la palabra "esmeril":** `"lija 100"` → lija normal (por hoja); `"lija esmeril 100"` → lija esmeril (por cm). No mezclarlos.
2. **Lija esmeril, cálculo correcto.** El `precio_venta` del catálogo es **por 100 cm**:
   - N°36 = $22.000 / 100 cm → **$220/cm**
   - N°60, N°80, N°100 = $20.000 / 100 cm → **$200/cm**
   - **Fórmula:** `total = cm × (precio_venta / 100)`
   - Ejemplos correctos: `10 cm lija esmeril 60` = **$2.000**; `100 cm lija esmeril 36` = **$22.000**; `50 cm lija esmeril 80` = **$10.000**.
   - **El dato del catálogo está bien** (es por 100 cm). NO cambiar precios; corregir solo la lógica de cálculo. Bug actual: `10 cm lija esmeril` devolvió **$500** (cálculo/match equivocado).
3. **Preguntar el N° (grit) si falta.** `"lija esmeril"` o `"10 cm lija esmeril"` sin número → **preguntar** "¿N°36, 60, 80 o 100?", NO cotizar un genérico. (Igual para lija normal sin número.)

### Aceptación (Bug 1)
- `10 cm lija esmeril 60` → registra/cotiza **$2.000** (cantidad = 10 cm, total = cm × precio/100).
- `lija esmeril 60` (sin cm) → pregunta cuántos cm.
- `lija esmeril` (sin N° ni cm) → pregunta el N°.
- `1 lija 60` (sin "esmeril") → lija **normal**, por hoja (~$2.000), no por cm.

---

## Bug 2 — Vinilo (y cuñetes): consulta de precio por TIPO, no por color

El precio depende del **tipo**, NO del color. Validado en datos: **todos** los `Vinilo Davinci T1 <color>` valen **$50.000** (Azul, Negro, Verde, Lila, Blanco… idéntico). El color es irrelevante para el precio.

Bug actual: ante `"cuanto vale el vinilo"` el bot listó ~10 **colores** y preguntó "¿cuál?". Eso es ruido inútil.

### Reglas a implementar
1. **Entender "t" = "tipo":** `t1` = `tipo 1`, `t2` = `tipo 2`, `t3` = `tipo 3` (a veces se acorta a solo la "t"). Mismo para cuñetes.
2. **Agrupar por tipo**, no por color. Tipos de la familia vinilo:
   - Vinilo T1 / T2 / T3
   - Cuñete Vinilo T1 / T2 / T3
   - ½ Cuñete Vinilo T1 / T2
3. **En consulta de precio, colapsar por precio/tipo:** si todos los candidatos que coinciden comparten el **mismo precio** (porque son el mismo tipo), **responder ese precio directo** sin enumerar colores. Ej.: `cuanto vale el vinilo davinci t1` → "Vinilo Davinci Tipo 1 (galón): $50.000. Fracciones: …". 
4. **Si el tipo es ambiguo, preguntar por TIPO** ("¿Tipo 1, Tipo 2 o Tipo 3?"), nunca por color.
5. **El color solo importa para inventario/venta** (qué SKU/stock descontar al registrar), NO para cotizar precio. Al **registrar una venta** sí puede pedirse el color (para el stock); al **consultar precio**, no.

### Aceptación (Bug 2)
- `cuanto vale el vinilo t1` (o "tipo 1") → responde **$50.000** + fracciones, sin listar colores.
- `cuanto vale el vinilo` (sin tipo) → pregunta "¿Tipo 1, 2 o 3?" (por tipo, no colores).
- `cuanto vale el cuñete vinilo t2` → precio del Cuñete Vinilo Tipo 2, sin listar colores.
- Registrar una venta de un color específico sigue funcionando (color → SKU/stock).

---

## Método y medición
- Trabajar por fases; al cierre correr `pytest` + el replay (`python -m tests.evals.replay.replay --corpus tests/evals/replay/corpus_puntorojo.jsonl --catalogo tests/evals/replay/catalogo_puntorojo.json`). No bajar la paridad ni introducir peligrosos.
- Añadir estos escenarios al eval (los de cálculo son medibles por el runner; los de consulta/desambiguación, validarlos por la ruta LLM o como checklist):

| frase | esperado |
|---|---|
| `10 cm lija esmeril 60` | venta, total $2.000 |
| `100 cm lija esmeril 36` | venta, total $22.000 |
| `lija esmeril 60` | pregunta cuántos cm |
| `lija esmeril` | pregunta el N° |
| `1 lija 60` | lija normal, por hoja (~$2.000) |
| `cuanto vale el vinilo t1` | $50.000, sin listar colores |
| `cuanto vale el vinilo` | pregunta por tipo (T1/T2/T3) |

- Anti-alucinación intacta: ante duda real (cm faltante, N° faltante, tipo ambiguo) → **preguntar**, nunca inventar.

## Referencia de código (dónde mirar)
- Cálculo de precio / unidades: `modules/inventario/precios.py`, `modules/inventario/models.py` (unidad_medida, precio_venta).
- Resolución/normalización de producto + bypass: `ai/bypass.py` (granel/unidad), `modules/inventario/busqueda.py`.
- Desambiguación / rieles: `ai/rieles.py`, `ai/dispatcher.py`. Para el colapso por tipo/precio en consultas, ahí es donde decide preguntar vs responder.
- Herramienta de consulta de precio: `ai/tools.py` (`consultar_producto`).
