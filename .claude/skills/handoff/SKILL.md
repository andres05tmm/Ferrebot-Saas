---
name: handoff
description: Cerrar la sesión dejando un handoff completo - estado exacto del repo (commit, ramas, PRs, migraciones), pendientes del owner persistidos a memoria, y prompt de arranque listo para la próxima sesión. Usar cuando el usuario diga "cerremos", "handoff", "dame el prompt para la próxima sesión", o antes de un /clear.
---

# /handoff — cierre de sesión con estado persistido

Produce dos artefactos: (A) una memoria persistente actualizada y (B) un prompt de arranque que el usuario copia para la próxima sesión. La meta: que la próxima sesión NO arranque con "resume la sesión anterior".

## 1. Recolectar estado real (no de memoria — verificar con comandos)

```bash
git status && git log --oneline -5 && git branch --show-current
gh pr list --state open                          # PRs abiertos y su CI
ls migrations/tenant/versions | sort | tail -3   # head de migraciones tenant
ls migrations/control/versions | sort | tail -3  # head de migraciones control
```

Más lo que sepas de la sesión: suites corridas y su resultado (números reales passed/failed), gates pendientes, deploys hechos o en curso.

## 2. Separar pendientes en dos listas

- **Pendientes de Claude** (retomables por la próxima sesión): tareas a medio hacer, tests por correr, PRs por mergear.
- **Pendientes del owner** (solo el usuario puede hacerlos): rotar secretos, correr comandos en Railway, verificar en el teléfono, decisiones de negocio. Estos son los que se pierden cuando se apaga el PC — SIEMPRE persistirlos.

## 3. Persistir a memoria

Actualizar (o crear) la memoria del trabajo en curso en el directorio de memoria persistente (`memory/` del proyecto en `~/.claude/projects/...`), con: estado exacto (commit, rama, PRs, heads de alembic), pendientes de ambas listas, y gotchas descubiertos en la sesión. Actualizar el índice `MEMORY.md`.

## 4. Prompt de arranque

Entregar al usuario un bloque copiable con esta forma:

```
Contexto: <qué se estaba haciendo, 2-3 líneas>.
Estado: rama <X> en commit <sha corto>, PR #<n> <estado>, migraciones head <NNNN>.
Lee antes: <rutas exactas de los archivos clave tocados>.
Siguiente paso: <la primera acción concreta>.
No toques: <alcance negativo si aplica>.
```

## 5. Cierre

Confirmar al usuario qué quedó persistido y dónde, y recordarle los pendientes que son SUYOS (lista del owner) de forma visible.
