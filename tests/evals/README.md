# Evals del agente

Harness de evaluación del runtime del agente (bypass + function calling), pensado para correr en CI
y blindar las **dos cosas críticas** del producto:

1. **Precisión de la llamada a herramienta** — dada una frase de mostrador (con typos, fracciones
   `1/2`, montos coloquiales como `20mil`), el agente llama a la herramienta correcta con los
   argumentos correctos.
2. **Aislamiento multi-tenant** — una herramienta ejecutada para la empresa A nunca lee/escribe en la
   base de la empresa B.

## Archivos

| Archivo | Qué evalúa |
|---|---|
| `corpus.py` | El corpus como datos: frases → herramienta/args esperados. Tres datasets (`PARSEO`, `DESPACHO`, `CONTRATO`). |
| `_harness.py` | Cableado compartido: catálogo fijo en memoria + fakes de repositorio + despachador REAL. Sin BD, sin LLM. |
| `test_function_call.py` | Corre los tres datasets del corpus contra `analizar` / `Bypass.intentar` / `Dispatcher.ejecutar`. |
| `test_aislamiento.py` | Ejecuta una herramienta para A por el camino real (despachador y bypass) y verifica que B queda intacta. Usa las bases efímeras de `conftest.py`. |

## Los tres planos de la precisión de llamada

- **PARSEO** (`ai.bypass.analizar`, función pura): texto → intención de venta o `CaeAlModelo(motivo)`.
  Cubre normalización de slug (typos de cantidad, plurales), fracciones (`1/2`, `1-1/2`, "medio") y
  los gates que desactivan el bypass (consulta, cliente, modificación, multiproducto).
- **DESPACHO** (`ai.bypass.Bypass.intentar`, con catálogo fijo): el camino rápido emite
  `registrar_venta` con `items=[{producto_id, cantidad}]` exactos, o **defiere al modelo** cuando no
  hay match confiable (typo de producto, precio escalonado, fracción inexistente). Es el gate del
  ~60 % de ventas sin IA.
- **CONTRATO** (`ai.dispatcher.Dispatcher.ejecutar`, con un `ToolCall` "gold"): el `ToolCall` que el
  modelo *debería* emitir para intenciones que el bypass no maneja (gasto/fiado con montos
  coloquiales). Verifica el contrato de la función: la herramienta existe, los args validan, y el
  RBAC/capacidad/confirmación cortan donde deben.

> **Límite explícito (montos coloquiales `20mil`):** traducir "20mil" → `20000` o "fiale a Pedro" →
> `registrar_fiado` es trabajo del **LLM en vivo**. En CI las claves de proveedor van vacías, así que
> ese mapeo lenguaje-natural→args NO se mide aquí; el corpus fija el `ToolCall` gold y comprueba el
> lado determinista (contrato + ejecución). La exactitud del LLM se evalúa aparte, con claves reales.

## Correr

```bash
# Solo los evals (rápido; el aislamiento necesita Postgres arriba)
pytest -m eval

# Solo los deterministas (sin BD)
pytest tests/evals/test_function_call.py
```

En CI corren en un paso dedicado (`pytest -m eval`) **antes** de la suite completa, para fallar
temprano si se rompe el corazón del producto, y además dentro de la suite completa.

## Extender el corpus

Agregar frases reales de mostrador a las tuplas de `corpus.py` (`PARSEO`, `DESPACHO`, `CONTRATO`).
Cada caso es un dato con su resultado esperado y `tags`; el test parametrizado los recoge solo.
