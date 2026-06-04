# Development Workflow

1. **Investigar y reusar** antes de escribir: buscar implementaciones y librerías probadas (GitHub, docs vía Context7/Exa, PyPI/npm). Preferir adoptar sobre reinventar.
2. **Planear:** usar `engineering:system-design` / `engineering:architecture` (ADRs) para decisiones; dividir en fases.
3. **TDD:** test primero (RED) → implementar (GREEN) → refactor.
4. **Revisar:** `engineering:code-review` apenas se escribe el código; corregir CRITICAL/HIGH.
5. **Commit y PR:** mensajes claros, plan de prueba, CI en verde (`engineering:deploy-checklist` antes de soltar).
