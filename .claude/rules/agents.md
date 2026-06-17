# Orquestación de agentes

Usar los skills del plugin `engineering` según la tarea:

| Tarea | Skill |
|---|---|
| Diseño de sistema / límites de servicio | `engineering:system-design` |
| Decisión con trade-offs (ADR) | `engineering:architecture` |
| Revisión de código | `engineering:code-review` |
| Estrategia de pruebas | `engineering:testing-strategy` |
| Depurar un error | `engineering:debug` |
| Checklist de despliegue | `engineering:deploy-checklist` |
| Documentación / runbooks | `engineering:documentation` |
| Incidentes / postmortem | `engineering:incident-response` |
| Deuda técnica | `engineering:tech-debt` |

Para tareas independientes, lanzar análisis en paralelo (seguridad, performance, tipos).

## Plugin `superpowers`

Sus skills se activan solas y aplican a todo el flujo: `brainstorming` (refinar antes de codear), `writing-plans` (trocear en tareas chicas), `requesting-code-review`, `systematic-debugging` y la orquestación de subagentes con git worktrees para trabajo en paralelo. Conviven con los skills de `engineering`.

**Una salvedad:** el TDD test-primero que empuja Superpowers se acota a los invariantes críticos; fuera de eso manda la cadencia código-primero. Ver `.claude/rules/development-workflow.md` y `.claude/rules/testing.md`.
