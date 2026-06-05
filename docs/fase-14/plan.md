# Fase 14 — Deploy a producción (Railway) · plan + troceo

> Fase 12 cerró la amplitud funcional (tabs fiscales + CRUD + reportes + RADIAN). Fase 14 lleva el
> producto a producción en **Railway** con una topología **LEAN** para el primer tenant (Punto Rojo),
> sin sobre-ingeniería: se añade infra/PgBouncer/multi-Postgres cuando el volumen lo pida.

## Topología LEAN (14a)

```
                 ┌──────────────────────────── Railway project ────────────────────────────┐
   tg webhook ──►│  bot (SERVICE_TYPE=bot)                                                   │
                 │  api (SERVICE_TYPE=api)  ◄── puntorojo.<BASE_DOMAIN> (TLS) ── navegador   │
                 │  worker (SERVICE_TYPE=worker)                                             │
                 │        │            │             │                                       │
                 │        └── Redis ◄──┘             └──► Postgres (control + tenants)       │
                 └──────────────────────────────────────────────────────────────────────────┘
```

- **3 servicios, 1 imagen.** `api`, `bot`, `worker` corren el **mismo** Docker image; difieren solo por
  `SERVICE_TYPE` (entrypoint ramifica). Ver `Dockerfile` + `docker-entrypoint.sh`.
- **UNA Postgres.** control DB y las app DB por empresa viven en la misma instancia (DB-per-tenant a nivel
  de *database*, no de servidor): `control` + `ferrebot_<slug>`. Conexiones **directas** (asyncpg), **sin
  PgBouncer** por ahora.
- **Un Redis** (cola ARQ + caché/dedup del bot).
- **Un subdominio** para Punto Rojo: `puntorojo.<BASE_DOMAIN>` → servicio `api` (que sirve el SPA y el API).
- **Secretos:** plataforma por env de Railway; por empresa, CIFRADOS en el control DB (re-cifrados bajo la
  master key de PROD al re-provisionar). Nada de secretos en la imagen ni en git.

## Troceo

| Slice | Alcance | Entregable | Estado |
|---|---|---|---|
| **14a** | **Artefactos de deploy** — Dockerfile multi-stage (dashboard + runtime), entrypoint por `SERVICE_TYPE`, `.dockerignore`, mapeo de env, runbook Railway | `Dockerfile`, `docker-entrypoint.sh`, `.dockerignore`, `.env.example`, `docs/fase-14/deploy-railway.md` | **🚧 en progreso** |
| 14b | **Primer deploy real** — crear proyecto, migrar control, re-provisionar PR, dominio + webhook, smoke en prod | (operación, sigue el runbook) | ⏳ pendiente |
| 14c | **Endurecimiento** — observabilidad (Sentry/logs), backups de Postgres, healthchecks Railway, CI build de la imagen | — | ⏳ pendiente |

> Más adelante (cuando el volumen lo pida): PgBouncer (pooling), Postgres dedicado por tenant grande,
> réplicas, y separar dominios por empresa. Ver `docs/infra-railway.md` y `docs/tenancy.md`.

---

## Slice 14a — Artefactos de deploy (en progreso)

### A — Imagen y entrypoint

- **Dockerfile** multi-stage:
  - *Stage 1 (`node:20-slim`)*: `npm ci` + `npm run build` del dashboard → `dashboard/dist`. El único valor
    horneado es `VITE_TELEGRAM_BOT_USERNAME` (ARG → el bot de PR). El build de prod usa `/api/v1` relativo y
    resuelve la empresa por subdominio (sin `X-Tenant-Slug`, que es solo de dev).
  - *Stage 2 (`python:3.12-slim`)*: deps por `uv sync --frozen --no-install-project --no-dev` (reproducible
    desde `uv.lock`); copia el código + `dashboard/dist`. El código se importa por `PYTHONPATH=/app` (no se
    empaqueta), para que `apps/api/main.py` resuelva `dashboard/dist` en la raíz.
- **`docker-entrypoint.sh`** ramifica por `SERVICE_TYPE`:
  - `api` → `uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --loop uvloop`
  - `bot` → `python -m apps.bot.main`
  - `worker` → `arq apps.worker.main.WorkerSettings`
- **`.dockerignore`**: excluye `node_modules`, `.venv`, `.git`, `dist` local, `__pycache__`, `.env*` (salvo
  `.env.example`), `tools/onboarding/*.json` (salvo el example), tests y docs.

### B — Mapeo de env a producción

`core/config/settings.py` consume (1:1 con `.env.example`): `ADMIN_DATABASE_URL`, `CONTROL_DATABASE_URL`,
`TENANTS_DIRECT_URL_BASE`, `SECRET_KEY`, `SECRETS_MASTER_KEY`, `BASE_DOMAIN`, `SERVICE_TYPE`, `REDIS_URL`,
`SENTRY_DSN`, `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`, `LLM_*`. En lean, las tres URLs Postgres apuntan a la
**misma** instancia (admin=`/postgres`, control=`/control`, tenants base sin `/db`). Tabla exacta en el
runbook.

### C — Runbook

`docs/fase-14/deploy-railway.md`: paso a paso accionable (proyecto + Postgres + Redis → 3 servicios →
variables → release command de migración → re-provisionar PR → dominio + webhook → smoke), con rollback y
el recordatorio de migraciones backward-compatible.

### Verificación (local, Docker)

`docker build --build-arg VITE_TELEGRAM_BOT_USERNAME=… ` → imagen OK; correr con `SERVICE_TYPE=api` contra
Postgres/Redis locales → `GET /health` responde `{"status":"ok"}`. `bot`/`worker`: el entrypoint arranca sin
crashear.
