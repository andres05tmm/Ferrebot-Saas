# Code Review

Revisar antes de mezclar. Bloquea si hay CRITICAL (seguridad/pérdida de datos).

## Checklist
- [ ] Resuelve el tenant y usa su sesión (multi-tenancy).
- [ ] Acceso a datos solo por repositorios; sin SQL suelto.
- [ ] `async/await` correcto en endpoints con eventos.
- [ ] Zona horaria Colombia.
- [ ] Sin secretos hardcodeados ni `print`.
- [ ] Manejo de errores explícito; defaults seguros.
- [ ] Funciones <50 líneas, archivos cohesivos; sin anidamiento profundo.
- [ ] Tests para lo nuevo; idempotencia donde aplique.
- [ ] Sin N+1 ni consultas sin límite.

Usar los skills `engineering:code-review` y `engineering:debug` cuando aplique.
