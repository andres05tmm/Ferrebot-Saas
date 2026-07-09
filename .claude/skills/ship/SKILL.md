---
name: ship
description: Llevar el trabajo actual a main de forma segura - commit atómico, push, PR con resumen y plan de prueba, watch de CI, merge en verde y verificación de redeploy en Railway. Usar cuando el usuario diga "shipea", "sube esto", "haz el PR y mergea", o al cerrar una feature lista.
---

# /ship — del working tree a main con CI verde

Ejecuta este flujo completo sin pedir confirmación entre pasos (solo detente si CI falla o hay conflictos).

## 1. Preparación

- Si estás en `main`, crea una rama primero: `git checkout -b tipo/descripcion` (tipos: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`). Hay un hook que bloquea commits directos a main.
- Revisa `git status` y `git diff` — commitea SOLO lo relacionado con la tarea; no arrastres archivos ajenos.

## 2. Commit

- Formato `tipo: descripción` (español, imperativo). **Sin atribución del asistente** (regla de `.claude/rules/git-workflow.md`): no incluir Co-Authored-By.
- Commits atómicos: si el diff mezcla temas separables, haz varios commits.

## 3. Push + PR

```bash
git push -u origin <rama>
gh pr create --title "tipo: descripción" --body "..."
```

El body del PR se arma analizando **todo** el historial de la rama (`git diff main...HEAD`, no solo el último commit):
- **Resumen** de qué cambia y por qué.
- **Plan de prueba** con pasos verificables.
- **Si hay migraciones nuevas** (archivos en `migrations/tenant/` o `migrations/control/`): agregar nota explícita "incluye migración XXXX — el preDeployCommand de Railway corre `migrate_tenants` solo, verificar que aplique a todos los tenants tras el deploy".

## 4. CI y merge

```bash
gh pr checks <numero> --watch    # check requerido: "test" (workflow CI, ~784 tests pytest)
gh pr merge <numero> --squash --delete-branch
```

- Merge SOLO con CI en verde. Si falla, lee el log (`gh run view <id> --log-failed`), corrige, push y vuelve a esperar.
- No usar `--admin` ni saltarse checks.

## 5. Verificar redeploy (Railway)

Tras el merge, Railway redespliega la API/bot (~2–5 min). Verificar:

```bash
git checkout main && git pull
# health de prod (dominio y servicios en docs/infra-railway.md):
curl -s https://<subdominio-tenant>.melquiadez.com/api/v1/../health  # o el /health del servicio api
```

- `/health` debe devolver `{"status": "ok"}`. Si el cambio incluyó un paquete top-level nuevo en Python, confirma que el Dockerfile tiene el `COPY` correspondiente (gotcha conocido: CI no lo detecta, prod crashea — ver memoria `dockerfile-copy-paquetes-runtime`).

## 6. Cierre

Reporta al usuario: PR mergeado (número + link), estado de CI, y si el redeploy quedó verificado o sigue en curso.
