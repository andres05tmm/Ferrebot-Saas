# Git Workflow

## Commits
Formato: `tipo: descripción`. Tipos: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`. Sin atribución del asistente.

## Ramas y PR
- `main` estable; trabajo en ramas `tipo/descripcion`.
- PR: analizar todo el historial de la rama (`git diff main...HEAD`), resumen claro y plan de prueba.
- Mezclar solo con CI en verde.
