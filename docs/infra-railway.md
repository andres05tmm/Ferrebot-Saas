# Topología de infraestructura (Railway)

> Cómo se despliega y conecta todo en Railway. Multi-tenancy en `tenancy.md`; jobs y DIAN en `facturacion-dian.md`; operación en `runbook.md`. Diagrama en `diagrams.md`.

## 1. Principios

- **Un repo, varios servicios.** El mismo código corre como `api`, `bot` o `worker` según `SERVICE_TYPE`. No se duplica lógica de dominio.
- **PgBouncer obligatorio.** El modelo DB-per-tenant multiplica conexiones; todo el CRUD pasa por PgBouncer en modo *transaction* (ver `tenancy.md` §5).
- **El plano de control es aparte del plano de datos.** El control DB (empresas, planes, secretos, branding) es una base; cada empresa tiene su app DB.
- **Jobs pesados fuera del request.** Emisión DIAN, provisioning y el runner de migraciones corren en el `worker` (Redis + ARQ), nunca en el hilo del request.
- **Sin self-ping.** El keepalive se eliminó; un monitor de uptime externo vigila `/health`.

## 2. Servicios

| Servicio | Origen | `SERVICE_TYPE` | Réplicas | Expuesto | Rol |
|---|---|---|---|---|---|
| `api` | repo (Dockerfile/Nixpacks) | `api` | 1→N | público (HTTPS, wildcard) | FastAPI: dashboard PWA, API v1, SSE, webhooks |
| `bot` | repo | `bot` | 1 | público (HTTPS) | Webhooks de Telegram `/tg/{slug}` (un bot por empresa) |
| `worker` | repo | `worker` | 1→N | privado | ARQ: emisión DIAN, conciliación, provisioning, `migrate_tenants` |
| `pgbouncer` | imagen Docker | — | 1 (HA luego) | privado | Pooling *transaction* delante de Postgres |
| `postgres-control` | Railway Postgres | — | 1 | privado | Control DB (plano de control) |
| `postgres-tenants` | Railway Postgres | — | 1→M | privado | App DBs de las empresas (muchas por instancia al inicio) |
| `redis` | Railway Redis | — | 1 | privado | Cola ARQ + caché compartida |

Externos (no en Railway): **monitor de uptime** (UptimeRobot/Betterstack → `/health`), **Sentry** (errores), **Cloudflare/DNS** (wildcard `*.app.dominio`), **MATIAS** y **Cloudinary** (por empresa), **Telegram**.

## 3. Topología

```
        Internet
           │  HTTPS
   ┌───────┴────────────────────────────────────────────┐
   │  *.app.dominio  ──────────────▶  [ api ]  (N réplicas)
   │  api.app.dominio (webhooks)        │  │  │
   │  tg.app.dominio ──▶ [ bot ]        │  │  └── SSE  ── LISTEN directo ─┐
   └─────────────────────┬─────────────┘  │                              │
                         │                 │ CRUD (transaction)           │ (sesión)
                         ▼                 ▼                              │
                    [ redis ]        [ pgbouncer ]  ◀──── [ worker ] ─────┤
                    (cola ARQ)            │  CRUD                         │
                         ▲                ▼                              ▼
                         └──── [ worker ] ──▶  postgres-control   postgres-tenants
                              (jobs)             (1 base)          (N bases de empresa)
```

- **CRUD** (api, bot, worker) → **PgBouncer** → Postgres. Pool por empresa pequeño (`pool_size=2, max_overflow=2`); PgBouncer multiplexa.
- **SSE / `LISTEN`** → **conexión directa** a `postgres-tenants` (PgBouncer *transaction* no soporta `LISTEN/NOTIFY`; ver §5).
- Toda comunicación entre servicios va por la **red privada** de Railway (`*.railway.internal`); solo `api` y `bot` exponen puertos públicos.

## 4. PgBouncer

- Imagen: `edoburu/pgbouncer` (o equivalente) como servicio propio.
- **Modo:** `pool_mode = transaction`. Permite cientos de conexiones de app sobre pocas reales de Postgres.
- **Presupuesto de conexiones:** `postgres-tenants` con `max_connections` acotado (p. ej. 100); PgBouncer reparte con `default_pool_size`, `max_db_connections`, `max_client_conn`. Regla: la suma de pools de app **nunca** supera lo que Postgres puede dar.
- **Driver:** sin prepared statements del lado servidor en *transaction mode* → `prepare_threshold=None` (psycopg) / `statement_cache_size=0` (asyncpg).
- **Una entrada por base:** PgBouncer enruta a la base de cada empresa; la URL cifrada de cada tenant (en `tenant_databases`) apunta al host de PgBouncer, no a Postgres directo.

## 5. Postgres (control + tenants)

- **`postgres-control`:** una sola base. Pequeña, crítica; la API la cachea (TTL corto, ver `tenancy.md` §3). Backups frecuentes.
- **`postgres-tenants`:** alberga **muchas app DBs** al inicio (una por empresa). Clientes grandes → instancia dedicada, transparente vía `tenant_databases.host`.
- **`CREATE DATABASE`** por empresa lo hace el provisioning (job ARQ) con una conexión admin directa (no por PgBouncer).
- **LISTEN/NOTIFY (clave):** el listener de SSE de cada empresa con suscriptores abre una **conexión de sesión directa** a `postgres-tenants` (saltándose PgBouncer o usando una instancia de PgBouncer en modo *session* dedicada). Sin suscriptores activos, no hay listener.
- **Escalado vertical primero** (más RAM/CPU a la instancia), luego **sharding por instancia** (mover bases de tenants pesados a otra instancia). El control DB es el directorio que hace esto transparente.

## 6. Redis + worker (ARQ)

- **Redis:** cola de ARQ y caché compartida entre réplicas (nunca caché en memoria del proceso para datos que deban ser consistentes multi-réplica; ver `.claude/rules/performance.md`).
- **Worker (ARQ):** procesa
  - `emitir_documento(factura_id)` — emisión DIAN con backoff y dead-letter.
  - `reconciliar_pendientes()` — job periódico (cron ARQ) que consulta estados DIAN sin webhook.
  - `provision_tenant(...)` — aprovisionar empresa (crear base → migrar → sembrar → secretos → admin → webhook).
  - `migrate_tenants()` — aplicar una migración tenant a todas las empresas (en el deploy).
- Escala con más réplicas de `worker`; los jobs son **idempotentes** (reintentables).

## 7. Networking y dominios

| Entrada | Apunta a | Notas |
|---|---|---|
| `*.app.dominio` (wildcard) | `api` | Subdominio = `slug` de la empresa (resolución de tenant). DNS/proxy con TLS wildcard. |
| `api.app.dominio` | `api` | Webhooks firmados: `POST /webhooks/matias`. |
| `tg.app.dominio` | `bot` | Webhooks de Telegram: `POST /tg/{slug}` (token por empresa). |
| `*.railway.internal` | servicios privados | PgBouncer, Postgres, Redis, worker — sin exposición pública. |

- Dominio propio por empresa (opcional) → `branding.dominio`, también enrutado a `api`.
- **SSE tras varias réplicas:** un navegador mantiene su stream contra **una** réplica de `api`; cada réplica con suscriptores de la empresa X mantiene su `LISTEN` sobre la base de X, así un `NOTIFY` llega a todas las réplicas que escuchan. No requiere sticky sessions para correctitud (sí ayuda para reusar el listener).

## 8. Variables de entorno

Plataforma (mismas en `api`/`bot`/`worker`, cambia `SERVICE_TYPE`). Por empresa **no** van aquí: viven **cifradas** en el control DB (`secretos_empresa`). Base en `.env.example`.

| Variable | Servicios | Nota |
|---|---|---|
| `SERVICE_TYPE` | todos | `api` \| `bot` \| `worker` |
| `CONTROL_DATABASE_URL` | todos | Apunta al **PgBouncer** (no a Postgres directo) |
| `TENANTS_PGBOUNCER_URL` | api, bot, worker | Host de PgBouncer para construir URLs de tenant |
| `TENANTS_DIRECT_URL` | api, worker | Conexión **directa** a Postgres para `LISTEN` (SSE) y `CREATE DATABASE` |
| `SECRET_KEY` | api, bot | Firma de JWT |
| `SECRETS_MASTER_KEY` | api, bot, worker | KEK que descifra secretos por empresa (idealmente KMS/Vault) |
| `BASE_DOMAIN` | api, bot | `app.dominio` para resolver subdominios |
| `REDIS_URL` | todos | Cola ARQ + caché (`*.railway.internal`) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | api, bot | IA (costo medido por empresa) |
| `SENTRY_DSN` | todos | Observabilidad |

## 9. Salud y readiness

- `GET /health` (en `api`): verifica **control DB** + dependencias (Redis, PgBouncer). Lo consume el monitor externo.
- `GET /ready`: el servicio está listo para atender (engines/cachés tibios).
- `worker`: healthcheck propio de ARQ (heartbeat en Redis).
- `bot`: el webhook responde 200 rápido; el trabajo pesado se delega a servicios/cola.

## 10. Despliegue

1. **Build** por servicio (Dockerfile o Nixpacks) desde el mismo repo; cada servicio difiere solo en el comando de arranque según `SERVICE_TYPE`.
   - `api`: `uvicorn apps.api.main:app` (uvloop), estáticos del dashboard servidos por FastAPI.
   - `bot`: `python -m apps.bot.main`.
   - `worker`: `arq apps.worker.main.WorkerSettings`.
2. **Migración control DB** en el *release command* del deploy: `alembic -c migrations/control/alembic.ini upgrade head`.
3. **Migración tenants** como **job ARQ** (`migrate_tenants`) disparado tras el deploy, no en el request. Itera empresas; si una falla, continúa y reporta (ver `tenancy.md` §7).
4. **Cero downtime:** migraciones backward-compatible (agregar antes de usar; cambios destructivos en dos pasos). Las réplicas viejas y nuevas deben tolerar el esquema intermedio.
5. **Rollback:** Railway redeploya la versión anterior; las migraciones backward-compatible permiten convivir. Nunca un `downgrade` destructivo automático en producción (ver `engineering:deploy-checklist`).

## 11. Escalado

- **`api`:** horizontal (réplicas) detrás del proxy de Railway. Stateless salvo los listeners SSE (ver §7).
- **`worker`:** horizontal según profundidad de cola.
- **PgBouncer:** primero vertical; HA con una segunda instancia cuando sea cuello de botella.
- **Postgres:** vertical, luego repartir tenants en más instancias (`tenant_databases.host`).
- **Redis:** Railway gestionado; vigilar memoria de la cola.

## 12. Backups y DR

- **Backups por base** + PITR en `postgres-tenants` y `postgres-control`. Restaurar una empresa a un punto en el tiempo **sin afectar a las demás** (ventaja del modelo DB-per-tenant).
- **Probar la restauración** periódicamente (runbook). Histórico fiscal DIAN: retención ~5 años, no se borra.
- Secretos: la `SECRETS_MASTER_KEY` se respalda fuera de la base (KMS/Vault); perderla = perder los secretos cifrados.

## 13. Observabilidad

- Logs estructurados con `tenant_id` + `request_id` en todos los servicios (nunca `print`, nunca secretos en logs).
- Sentry con contexto de empresa. Métricas por empresa (costo IA, tasa de bypass, profundidad de cola, conexiones PgBouncer).
- Alertas: dead-letter de DIAN, `too many connections` en PgBouncer, fallos de `migrate_tenants`, caída de `/health`.

## 14. Notas de costo

- Empezar con **una** instancia de cada cosa (api 1 réplica, 1 worker, 1 PgBouncer, control + tenants compartidos, Redis). Crecer por demanda.
- Billing a empresas: manual por ahora (ver `architecture.md` §15); la infra no depende de ningún proveedor de pagos.
