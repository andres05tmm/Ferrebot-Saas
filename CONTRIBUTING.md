# Contribuir

## Flujo

1. Investigar y reusar antes de escribir (ver `.claude/rules/development-workflow.md`).
2. Planear → TDD (test primero) → implementar → revisar.
3. Tests verdes y cobertura razonable antes de mezclar a `main`.

## Commits

Formato: `tipo: descripción` — tipos: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`. Sin atribución del asistente.

## Ramas

`main` estable. Trabajo en ramas `tipo/descripcion-corta`. PR con resumen y plan de prueba.

## Reglas

Ver `.claude/rules/` — en especial `multitenancy.md` (aislamiento) y `security.md` (secretos).
