# Development Workflow

1. **Investigar y reusar** antes de escribir: buscar implementaciones y librerías probadas (GitHub, docs vía Context7/Exa, PyPI/npm). Preferir adoptar sobre reinventar.
2. **Planear:** usar `engineering:system-design` / `engineering:architecture` (ADRs) para decisiones; dividir en fases.
3. **Código primero, tests al cierre de fase.** Implementar libremente dentro de la fase sin frenar por tests. Al terminar cada fase, escribir y correr **toda** la suite de la fase (`pytest`), revisar y corregir lo que falle antes de avanzar a la siguiente. (Antes era TDD test-primero; se cambió para dar más fluidez a la generación de código.)
4. **Revisar:** `engineering:code-review` apenas se escribe el código; corregir CRITICAL/HIGH.
5. **Commit y PR:** mensajes claros, plan de prueba, CI en verde (`engineering:deploy-checklist` antes de soltar).
> **Carve-out (no negociable):** aunque los tests van al cierre de fase, los invariantes críticos —aislamiento multi-tenant, idempotencia, y "nada mueve stock/caja sin movimiento"— deben quedar cubiertos por la suite de la fase antes de mezclar a `main`.