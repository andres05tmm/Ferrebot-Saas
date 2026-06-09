# Runbook — Deploy a Railway (topología LEAN)

> Despliegue de FerreBot SaaS en Railway para el primer tenant (Punto Rojo): **3 servicios, 1 imagen, 1
> Postgres, 1 Redis, 1 subdominio**. Sin PgBouncer (conexiones directas). Pensado para que el operador lo
> ejecute paso a paso. La imagen y el entrypoint viven en `Dockerfile` + `docker-entrypoint.sh`.

## 0. Prerrequisitos

- Cuenta Railway + Railway CLI opcional (`railway`), o todo desde el dashboard web.
- Repo accesible por Railway (GitHub) **o** deploy por CLI.
- El JSON de onboarding de Punto Rojo (`tools/onboarding/puntorojo.json`) con sus secretos reales. **NO está
  en git** (`.gitignore` lo excluye); súbelo de forma segura solo al momento de re-provisionar (paso 5).
- El **username del bot de Telegram de PR** (para hornear el widget de login) y su **token** (para el webhook).
- `MATIAS_AMBIENTE=pruebas` para PR hasta validar emisión/RADIAN en producción.

## 1. Crear proyecto + Postgres + Redis

1. Railway → **New Project**.
2. **+ New → Database → PostgreSQL**. Quedará `DATABASE_URL` tipo
   `postgresql://postgres:<pass>@<host>:<port>/railway`.
3. **+ New → Database → Redis**. Quedará `REDIS_URL`.

> En lean usamos UNA Postgres: el control DB y las app DB por empresa son *databases* dentro de esta misma
> instancia. La DB `railway` que crea Railway no se usa como tal; creamos `control` y `ferrebot_<slug>`.

## 2. Crear los 3 servicios (misma imagen, distinto SERVICE_TYPE)

Crea **tres** servicios desde el **mismo repo** (todos usan el `Dockerfile` de la raíz):

| Servicio | `SERVICE_TYPE` | Proceso (entrypoint) | Expone |
|---|---|---|---|
| `api` | `api` | `uvicorn apps.api.main:app … --loop uvloop` | HTTP público (dominio) |
| `bot` | `bot` | `python -m apps.bot.main` (webhook) | HTTP (webhook de Telegram) |
| `worker` | `worker` | `arq apps.worker.main.WorkerSettings` | — (sin HTTP) |

En cada servicio:
- **Build**: Dockerfile (Railway lo detecta). En el servicio `api`, define el **build arg**
  `VITE_TELEGRAM_BOT_USERNAME` = el username del bot de PR (se hornea en el bundle del dashboard). En `bot`
  y `worker` el dashboard no se usa, pero la imagen es la misma (no pasa nada si el arg va vacío ahí).
- **Networking**: solo `api` (y `bot`, para el webhook) necesitan dominio/puerto público. `worker` no expone
  puerto.

## 3. Variables por servicio

Railway expone `DATABASE_URL` y `REDIS_URL` de los plugins (referéncialas con `${{Postgres.DATABASE_URL}}` /
`${{Redis.REDIS_URL}}`). Derivamos las tres URLs Postgres de la **misma** instancia. Sustituye
`USER/PASS/HOST/PORT` por los de tu `DATABASE_URL`.

| Variable | Valor (lean) | api | bot | worker |
|---|---|:--:|:--:|:--:|
| `SERVICE_TYPE` | `api` / `bot` / `worker` | ✅ | ✅ | ✅ |
| `ADMIN_DATABASE_URL` | `postgresql://USER:PASS@HOST:PORT/postgres` | ✅ | ✅ | ✅ |
| `CONTROL_DATABASE_URL` | `postgresql://USER:PASS@HOST:PORT/control` | ✅ | ✅ | ✅ |
| `TENANTS_DIRECT_URL_BASE` | `postgresql://USER:PASS@HOST:PORT` (sin `/db`) | ✅ | ✅ | ✅ |
| `SECRET_KEY` | **generar nuevo** (ver abajo) | ✅ | ✅ | ✅ |
| `SECRETS_MASTER_KEY` | **generar nuevo** (ver abajo) | ✅ | ✅ | ✅ |
| `BASE_DOMAIN` | `<BASE_DOMAIN>` (p. ej. `ferrebot.app`) | ✅ | ✅ | ✅ |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | ✅ | ✅ | ✅ |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | claves de plataforma | ✅ | ✅ | ✅ |
| `SENTRY_DSN` | opcional | ✅ | ✅ | ✅ |
| `DEFAULT_TENANT_SLUG` | `puntorojo` — **solo en la variante SIN dominio propio** (ver abajo) | ⚠️ | ⚠️ | — |
| `VITE_TELEGRAM_BOT_USERNAME` | **build arg** del servicio `api` (no runtime) | (build) | — | — |

> **Por qué las 3 URLs en todos los servicios:** `core/config/settings.py` exige `ADMIN_DATABASE_URL`,
> `CONTROL_DATABASE_URL` y `TENANTS_DIRECT_URL_BASE` siempre (el `Settings` falla al arrancar si falta
> alguna). `worker`/`bot` también resuelven empresas y abren la app DB del tenant, así que las necesitan.
>
> **`worker` corre el job de provisioning del panel (ADR 0010 §B2):** es la pieza pesada/privilegiada
> (`CREATE DATABASE "ferrebot_<slug>"` + cifrado de secretos por empresa). Por eso el `worker` necesita
> sí o sí `ADMIN_DATABASE_URL` (superusuario para `CREATE DATABASE`) y `SECRETS_MASTER_KEY` (cifrar los
> secretos del manifiesto en el control DB) en su entorno —ya listadas arriba con ✅ para `worker`.

### Variante SIN dominio propio (deploy single-tenant en el dominio de Railway)

Si **todavía no** tienes el dominio `puntorojo.<BASE_DOMAIN>` y quieres salir rápido usando el dominio que
da Railway (p. ej. `ferrebot-api-production.up.railway.app`), hay un detalle: en ese host **no hay
subdominio** que identifique la empresa, y el **login** ocurre *antes* de tener un JWT, así que el
`TenantMiddleware` no puede resolver el tenant por subdominio ni por claim. Además, **el dashboard de
producción NO envía `X-Tenant-Slug`** (ese header es solo de desarrollo). Para cubrirlo:

- Setea **`DEFAULT_TENANT_SLUG=puntorojo`** en el servicio `api` (y en `bot` si su webhook no llega por un
  subdominio con el slug). Es el **último recurso** de `resolve_slug`: solo aplica cuando nada explícito
  resolvió, y las señales explícitas (subdominio / `X-Tenant-Slug` / claim del JWT) **siguen ganando**. Es
  **opt-in**: sin esta variable el comportamiento es el multi-tenant de siempre (sin fallback).
- En esta variante puedes omitir el paso 6 (dominio personalizado) y usar la URL de Railway directamente. El
  `BASE_DOMAIN` puede quedar en el dominio que planeas usar a futuro (no afecta mientras uses el de Railway).

> **Al pasar a multi-tenant** (segunda empresa): **quita `DEFAULT_TENANT_SLUG`** y configura el dominio con
> **wildcard de subdominios** (`*.<BASE_DOMAIN>`) para que cada empresa resuelva por su `slug.<BASE_DOMAIN>`
> (ver paso 6). El fallback es exclusivamente para el caso single-tenant sin dominio.

### Generar SECRET_KEY y SECRETS_MASTER_KEY para PROD

**No reutilices** las locales. Genera nuevas y guárdalas en un gestor seguro (si pierdes
`SECRETS_MASTER_KEY` los secretos cifrados por empresa quedan irrecuperables):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"   # SECRETS_MASTER_KEY
```

## 4. Release command del servicio `api` — migrar el control DB

Crea primero la **database** `control` en la instancia (una vez), por ejemplo con `psql` contra la URL de
Railway:

```bash
psql "postgresql://USER:PASS@HOST:PORT/postgres" -c 'CREATE DATABASE control;'
```

Configura el **Release Command** del servicio `api` (corre antes de cada release, con las env del servicio):

```bash
alembic -c migrations/control/alembic.ini upgrade head
```

Esto crea/actualiza el esquema del control DB (empresas, planes, branding, `secretos_empresa`,
`config_empresa`). Las app DB por empresa se migran al provisionar (paso 5) y, para tenants ya existentes,
con `python -m tools.migrate_tenants`.

## 5. Re-provisionar Punto Rojo en PROD

El provisioning **re-cifra** los secretos de PR bajo la `SECRETS_MASTER_KEY` de PROD, así que hay que
correrlo en el entorno de Railway (con sus variables). Sube `puntorojo.json` de forma segura (NO a git) y
ejecútalo en un *one-off* del servicio (Railway: `railway run` o un shell del servicio `api`):

```bash
python -m tools.provision_tenant --from tools/onboarding/puntorojo.json
```

Esto: crea `ferrebot_puntorojo` (vía `ADMIN_DATABASE_URL`), la migra, siembra, registra la empresa en el
control DB, **cifra** MATIAS/Cloudinary/token con la master key de PROD y fija el `telegram_id` del admin.
Es idempotente. Verifica que el JSON lleve `config.matias_ambiente = "pruebas"`.

> Si el JSON tiene secretos sensibles, bórralo del contenedor tras provisionar. Nunca lo dejes en la imagen
> ni lo subas al repo.

## 6. Dominio + webhook del bot

1. **Dominio del API**: en el servicio `api`, agrega un dominio personalizado `puntorojo.<BASE_DOMAIN>` y
   crea el registro DNS (CNAME al dominio de Railway). Railway provee TLS. El SPA y el API quedan en ese
   host; el `TenantMiddleware` resuelve la empresa por el subdominio `puntorojo` (`core/tenancy/resolver.py`).
2. **Webhook de Telegram**: el servicio `bot` recibe en `/tg/{slug}` (`apps/bot/main.py`). Registra el
   webhook del bot de PR apuntando a la URL pública del servicio `bot`:

   ```bash
   curl -X POST "https://api.telegram.org/bot<TOKEN_PR>/setWebhook" \
        -d "url=https://<dominio-del-servicio-bot>/tg/puntorojo"
   ```

   (El `<TOKEN_PR>` es el del bot de PR; ya quedó cifrado en el control DB al provisionar — este `setWebhook`
   es una acción de Telegram, no de la app.)

## 7. Smoke de producción

1. Abre `https://puntorojo.<BASE_DOMAIN>` → carga el dashboard (SPA) tematizado con el branding de PR.
2. **Login** con el widget de Telegram (el del bot horneado) usando tu `telegram_id` (el admin sembrado).
3. Verifica que el shell carga (`GET /api/v1/config` devuelve features + branding) y que se ven los tabs.
4. `GET https://puntorojo.<BASE_DOMAIN>/health` → `{"status":"ok"}`; `/ready` → `200` con `control_db` y
   `redis` en `ok`.
5. (Opcional) envía un mensaje al bot de PR en Telegram → confirma que responde (webhook vivo).

---

## Rollback

- **Redeploy de la versión anterior:** en Railway, cada servicio guarda el historial de deploys. Ante un
  fallo, **Redeploy** del deploy previo (imagen + variables) en `api`/`bot`/`worker`. Es la vía rápida.
- **Migraciones:** el rollback de imagen NO revierte el esquema. Por eso las migraciones deben ser
  **backward-compatible** (la versión vieja del código debe poder correr contra el esquema nuevo): añadir
  columnas/tablas, nunca borrar/renombrar en el mismo release que el código que aún las usa. Si hace falta
  revertir un esquema, hazlo con una migración `downgrade` deliberada, no con el redeploy.
- **Datos:** habilita backups automáticos de la Postgres de Railway (Fase 14c) antes de operaciones
  destructivas; el histórico fiscal DIAN se conserva ~5 años (`SECURITY.md`).

## Notas operativas

- **Una sola Postgres**: vigila el tope de conexiones (sin PgBouncer cada proceso abre conexiones directas).
  Si se queda corto, sube el plan de Postgres o introduce PgBouncer (ver `docs/infra-railway.md`).
- **`MATIAS_AMBIENTE`**: PR arranca en `pruebas`. Cambiar a `produccion` (en `config_empresa`, vía
  re-provisionar o un update) es una decisión a conciencia: emite/eventúa documentos DIAN REALES.
- **Verificación local de la imagen** (antes de subir): ver §Verificación en `docs/fase-14/plan.md`.
