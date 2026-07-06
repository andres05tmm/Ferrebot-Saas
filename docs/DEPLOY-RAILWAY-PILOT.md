# Desplegar a Railway — PILOTO (1–2 tenants)

Runbook **lean** para sacar el SaaS a producción para un piloto (1–2 clínicas), **no** la topología
completa de `docs/infra-railway.md`. Difiere PgBouncer y la separación control/tenants en instancias
distintas (ver §9 *Para escala*). Cubre solo el canal **WhatsApp** (agente); **sin** el servicio bot
de Telegram.

> Convención: **[UI]** = en el dashboard de Railway · **[CLI]** = consola (Railway CLI / local).
> La misma imagen del repo sirve API y Worker; difieren solo por `SERVICE_TYPE` (ver `docker-entrypoint.sh`).

---

## 0. Topología del piloto

```
Internet ──HTTPS──▶ [ API ]  (SERVICE_TYPE=api)  ── enqueue ──▶ [ Redis ]
                       │  │                                         ▲
   Kapso ─webhook─▶ /wa/webhook                                     │ ARQ
                       │  └── SPA + /api/v1 + SSE                    │
                       ▼                                       [ Worker ] (SERVICE_TYPE=worker)
                 [ Postgres ]  ◀── control DB + app DBs de tenants ──┘   └─▶ Kapso (envío) + Claude
```

- **2 servicios** desde el repo (misma imagen Docker): **API** y **Worker**.
- **1 Postgres** (aloja el control DB `ferrebot_control` **y** las app DBs `ferrebot_<slug>`).
- **1 Redis** (cola ARQ).
- El **API** valida y encola los webhooks de Kapso; el **Worker** corre el agente (LLM + herramientas)
  y responde por Kapso. Sin PgBouncer: las conexiones van **directas** a Postgres (a este volumen va
  sobrado, y además el `LISTEN/NOTIFY` de SSE funciona nativo).

---

## 1. Prerrequisitos

- Cuenta Railway + proyecto nuevo. **[UI]**
- Railway CLI instalado y logueado: `npm i -g @railway/cli && railway login`. **[CLI]**
- En local: el repo, el venv `.venv` con las deps (`uv sync`) y `psql` disponible — solo para el
  bootstrap de una sola vez (§5–6).
- Una cuenta **Kapso** con un número de WhatsApp y su **API key** + **webhook secret**.
- Una **Anthropic API key** (Claude).

Genera dos secretos fuertes y **estables** (no los cambies después: `SECRETS_MASTER_KEY` descifra los
secretos por empresa; si cambia, todo lo cifrado se vuelve ilegible): **[CLI]**

```bash
python -c "import secrets; print('SECRET_KEY        =', secrets.token_urlsafe(48))"
python -c "import secrets; print('SECRETS_MASTER_KEY=', secrets.token_urlsafe(32))"
```

---

## 2. Crear los recursos en Railway  **[UI]**

En el proyecto:

1. **+ New → Database → Add PostgreSQL** → queda el servicio `Postgres`.
2. **+ New → Database → Add Redis** → queda el servicio `Redis`.
3. **+ New → GitHub Repo** → elige este repo → crea el servicio **API**.
   - Railway detecta el `Dockerfile`. Settings → **Networking → Generate Domain** (dominio público
     HTTPS para el API y el webhook de Kapso).
4. Duplica para el **Worker**: **+ New → GitHub Repo** → mismo repo → servicio **Worker**.
   - **No** generes dominio (privado). El arranque lo decide `SERVICE_TYPE=worker`.

Ambos servicios construyen la **misma imagen**; solo cambia `SERVICE_TYPE` (§3).

---

## 3. Variables de entorno por servicio  **[UI]**

Nombres = campos de `core/config/settings.py` en MAYÚSCULAS. **Obligatorias (sin default → el proceso
no arranca):** `ADMIN_DATABASE_URL`, `CONTROL_DATABASE_URL`, `TENANTS_DIRECT_URL_BASE`. Las demás
tienen default de dev y **hay que fijarlas en prod** para que el JWT, el cifrado y el agente funcionen.

Usa **referencias** a los plugins (Railway resuelve `${{Postgres.*}}` y `${{Redis.*}}`).

### Comunes a API y Worker (plataforma)

| Variable | Valor (referencia Railway) | Obligatoria |
|---|---|---|
| `ADMIN_DATABASE_URL` | `postgresql://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}` | **sí** (CREATE DATABASE de tenants) |
| `CONTROL_DATABASE_URL` | `postgresql://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/ferrebot_control` | **sí** |
| `TENANTS_DIRECT_URL_BASE` | `postgresql://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}` *(sin /db)* | **sí** |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | sí en prod (cola ARQ) |
| `SECRETS_MASTER_KEY` | el generado en §1 (estable) | sí en prod |
| `SENTRY_DSN` | tu DSN (opcional) | no |

> Nota: `${{Postgres.PGHOST}}` es el host **privado** (`*.railway.internal`). Mantén estos valores en
> ambos servicios para que el tráfico vaya por la red privada.

### Solo API (`SERVICE_TYPE=api`)

| Variable | Valor | Obligatoria |
|---|---|---|
| `SERVICE_TYPE` | `api` | **sí** |
| `SECRET_KEY` | el generado en §1 | sí (firma JWT) |
| `BASE_DOMAIN` | tu dominio (p. ej. `<api>.up.railway.app`) | recomendado |
| `DEFAULT_TENANT_SLUG` | `clinica-demo` | sí para piloto de **1 tenant** (ver §9) |
| `KAPSO_WEBHOOK_SECRET` | el de Kapso | sí (valida la firma del webhook) |

### Solo Worker (`SERVICE_TYPE=worker`)

| Variable | Valor | Obligatoria |
|---|---|---|
| `SERVICE_TYPE` | `worker` | **sí** |
| `LLM_PROVIDER` | `claude` | sí |
| `LLM_MODEL_ORQUESTADOR` | `claude-sonnet-4-6` | sí (turno del agente) |
| `LLM_MODEL_WORKER` | `claude-haiku-4-5-20251001` | sí |
| `ANTHROPIC_API_KEY` | tu key de Claude | sí (key de plataforma) |
| `KAPSO_API_KEY` | tu Project API key de Kapso | sí (envío saliente) |
| `KAPSO_API_BASE` | `https://api.kapso.ai/meta/whatsapp/v24.0` | no (tiene default) |

> El **API** no llama al LLM ni a Kapso-envío (solo valida y encola), por eso sus keys van en el
> **Worker**. `SECRET_KEY` solo lo usa el API (JWT).

---

## 4. Build / arranque

No tienes que tocar el comando: el `Dockerfile` y `docker-entrypoint.sh` ramifican por `SERVICE_TYPE`
(`api` → `uvicorn apps.api.main:app` en `$PORT`; `worker` → `arq apps.worker.main.WorkerSettings`).
Con las variables puestas, **Deploy** cada servicio. **[UI]**

⚠️ El entrypoint **NO corre migraciones**. Las migraciones van aparte (§5).

---

## 5. Migraciones (orden: control → tenants)

El orden es **siempre**: primero el **control DB** (Alembic), luego las **app DBs** de los tenants
(runner). El árbol control lee `CONTROL_DATABASE_URL`; el runner de tenants lee el control DB y aplica
`upgrade head` a cada empresa.

### 5.1 Bootstrap de una sola vez (crear el control DB + migrarlo)

El control DB `ferrebot_control` aún no existe. Córrelo **dentro de la red de Railway** (los hosts son
privados). La forma más simple es exec en el contenedor del API ya desplegado: **[CLI]**

```bash
railway link                     # elige el proyecto y el servicio API
railway ssh                      # entra al contenedor del API (red privada, env presente)
# Ya dentro del contenedor:
python - <<'PY'
import psycopg
from core.config import get_settings
from core.db.urls import to_libpq
s = get_settings()
with psycopg.connect(to_libpq(s.admin_database_url), autocommit=True) as c:
    if not c.execute("SELECT 1 FROM pg_database WHERE datname='ferrebot_control'").fetchone():
        c.execute('CREATE DATABASE ferrebot_control')
        print('ferrebot_control creado')
    else:
        print('ferrebot_control ya existía')
PY
alembic -c migrations/control/alembic.ini upgrade head
```

> Si tu plan no tiene `railway ssh`: alternativa **[UI]** — en el servicio API, *Settings → Deploy →
> Custom Start Command*, pega temporalmente el bloque de arriba seguido de `&& sleep infinity`,
> redeploy, revisa los logs, y **restaura** el start command vacío (vuelve al entrypoint).

### 5.2 Migraciones en cada deploy posterior (automático)

Para no repetir a mano en cada cambio de esquema, pon un **Pre-deploy Command** en el servicio API:
**[UI]** *Settings → Deploy → Pre-deploy Command*:

```
alembic -c migrations/control/alembic.ini upgrade head && python -m tools.migrate_tenants
```

Corre dentro de la red privada antes de arrancar la versión nueva. `migrate_tenants` itera las
empresas del control DB y aplica `upgrade head` a cada app DB (si una falla, sigue y reporta). Con 0
tenants es un no-op, así que es seguro desde el primer deploy.

> **Config-as-code (en el repo):** `railway.api.toml` ya trae este `preDeployCommand` + el healthcheck
> `/health`; `railway.toml` (base) es para el **Worker** (sin pre-deploy, sin health HTTP). Asigna a
> cada servicio su archivo en *Settings → Config-as-Code → Config Path* (API → `railway.api.toml`,
> Worker → `railway.toml`) para que las migraciones corran en **un solo** servicio. `docker-entrypoint.sh`
> tiene un *passthrough* (`[ "$#" -gt 0 ] && exec "$@"`), así que el `preDeployCommand` se ejecuta tal
> cual (no lo traga el ENTRYPOINT); el arranque normal corre sin args y sigue por `SERVICE_TYPE`.

---

## 6. Provisionar y sembrar el tenant del piloto + flags

Crea la empresa, su app DB (migrada), el catálogo de la clínica demo, y enciende **pack_agenda** +
**canal_whatsapp**. Idempotente. Córrelo **dentro de la red** (para que la URL del tenant guardada use
el host privado). Con `railway ssh` en el contenedor del API o del Worker: **[CLI]**

```bash
railway ssh           # contenedor del API o Worker
python -m tools.seed_clinica_demo
```

`seed_clinica_demo` aprovisiona el tenant `clinica-demo` (2 profesionales, 3 servicios, disponibilidad
L–V, reglas en modo_confirmacion=manual) y enciende los dos flags. Imprime el slug.

> El SECRETS_MASTER_KEY del contenedor es el mismo que usan los servicios → los secretos por empresa
> se cifran/descifran consistentes. (Otro tenant del piloto: `python -m tools.provision_tenant <slug>
> "<Nombre>" <nit>` y repite los pasos de catálogo/flags con sus datos.)

---

## 7. Conectar Kapso (entrada + salida)

1. En **Kapso**, configura el **webhook** del número apuntando a tu API público: **[UI Kapso]**

   ```
   https://<tu-dominio-del-API>/wa/webhook
   ```

   con el mismo **webhook secret** que pusiste en `KAPSO_WEBHOOK_SECRET` (el API valida la firma
   HMAC-SHA256 del cuerpo).

2. **Mapea el número → tenant** en el control DB. Esto solo escribe en el control DB, así que puedes
   correrlo en-red (recomendado) o desde local con `CONTROL_DATABASE_URL` apuntando al control de prod:
   **[CLI]**

   ```bash
   railway ssh
   python -m tools.seed_wa_numero <phone_number_id> clinica-demo
   ```

   El `<phone_number_id>` lo da Kapso (dashboard / payload del webhook).

---

## 8. Verificación

1. **Salud del API** — desde cualquier lado: **[CLI]**
   ```bash
   curl https://<tu-dominio-del-API>/health      # → {"status":"ok"}
   curl https://<tu-dominio-del-API>/ready       # → {"status":"ready", "checks": {...}}  (control DB + Redis)
   ```
2. **`/config` del tenant** — confirma que las features están activas. Mintea un JWT del admin del
   tenant (mismo `tools.dev_token`, corre en-red) y consúltalo: **[CLI]**
   ```bash
   railway ssh
   python -m tools.dev_token clinica-demo     # imprime el JWT del admin
   # con ese token (DEFAULT_TENANT_SLUG resuelve la empresa sin subdominio):
   curl -H "Authorization: Bearer <JWT>" https://<tu-dominio-del-API>/api/v1/config
   # → debe incluir "pack_agenda" (y "canal_whatsapp") en features
   ```
3. **Mensaje de prueba por WhatsApp** — desde tu teléfono, escríbele al número del piloto algo como
   *"¿Qué servicios tienen?"* y luego *"Quiero una limpieza mañana en la tarde"*. Debes recibir
   respuesta del agente. **[manual]**
   - Sigue el rastro en los **logs** del API (`wa_mensaje_procesado`) y del Worker (`atender_mensaje_wa`,
     envío por Kapso). Como `modo_confirmacion=manual`, la cita entra **pendiente** (se aprueba desde
     el dashboard o por API).

---

## 9. Para escala (diferido en el piloto)

- **PgBouncer** (modo *transaction*) delante de Postgres cuando crezcan los tenants/conexiones
  (`docs/infra-railway.md` §4–5). El SSE necesitará una conexión **directa** para `LISTEN/NOTIFY`.
- **postgres-tenants separado** del control DB (instancias distintas) — transparente vía
  `tenant_databases.host`.
- **Subdominios + wildcard DNS** (`*.app.dominio` → API) para 2+ tenants sin `DEFAULT_TENANT_SLUG`:
  el SPA de prod resuelve la empresa por **subdominio** (= slug). Con 1 tenant, `DEFAULT_TENANT_SLUG`
  evita el wildcard.
- **Réplicas** del API/Worker y **backups** automáticos del Postgres.

---

## 10. Riesgos / huecos detectados (revisar antes de prod)

1. **El entrypoint no migra.** `docker-entrypoint.sh` solo arranca el proceso; las migraciones van por
   el **Pre-deploy Command** (§5.2) o un one-off. Si despliegas un cambio de esquema **sin** correr la
   migración, el API/Worker fallarán contra el esquema viejo. *Mitigación:* deja el Pre-deploy Command
   puesto siempre.
2. **Provisioning = `CREATE DATABASE`.** Requiere que el rol de Postgres tenga `CREATEDB` (el usuario
   por defecto de Railway lo tiene). Con un rol restringido, `provision_tenant` falla.
3. **Host de la URL del tenant.** La URL de conexión que se **guarda cifrada** se compone de
   `TENANTS_DIRECT_URL_BASE`. Por eso el provisioning debe correrse **en-red** (host privado); si lo
   corres desde local con el host público, los servicios alcanzarán las app DBs por el proxy público
   (funciona, pero con un salto extra). Re-provisionar en-red lo corrige.
4. **Login del dashboard en prod = Telegram Login Widget.** El piloto WhatsApp **no** configura el bot
   de Telegram, así que para *ver* el dashboard hace falta, además, el `telegram_token` de la empresa y
   el `telegram_id` del admin (en `secretos_empresa` / la base del tenant). La verificación de §8 usa un
   JWT minteado, no el widget. *(El agente de WhatsApp no depende de esto.)*
5. **Un solo Postgres.** Control DB y app DBs comparten instancia: vigila `max_connections` y activa
   **backups** de Railway. A escala de piloto sobra; el modelo DB-per-tenant multiplica conexiones al
   crecer (de ahí PgBouncer en §9).
6. **El Worker no expone health HTTP.** Su salud es el heartbeat de ARQ en Redis; monitoréalo por los
   **logs**/métricas de Railway (no por un endpoint).
7. **Secretos estables.** Cambiar `SECRETS_MASTER_KEY` tras provisionar deja ilegibles los secretos por
   empresa (incl. la URL de conexión del tenant). Trátalo como inmutable; respáldalo fuera de Railway.

---

## 10. Punto Rojo a producción — servicio Bot de Telegram + Bancolombia + corte

Punto Rojo es un tenant **retail** con bot de **Telegram** (no WhatsApp), así que su corte a producción
añade cosas que el piloto WhatsApp no cubre: un **tercer servicio** (`SERVICE_TYPE=bot`), la ingesta
**Bancolombia por Gmail**, la migración de datos (**ETL**) y el **corte de webhook** del bot viejo al
nuevo. La lógica del corte de Telegram está en `docs/migracion-puntorojo.md §10`; esto es el runbook de
infraestructura que lo rodea.

### 10.1 Servicio Bot de Telegram

Tercer servicio Railway sobre **la misma imagen** (igual que API/Worker), diferenciado por env var:

- **[UI]** New Service → mismo repo → **Variables**: copia las comunes (`*_DATABASE_URL`,
  `TENANTS_DIRECT_URL_BASE`, `REDIS_URL`, `SECRET_KEY`, `SECRETS_MASTER_KEY`) y añade `SERVICE_TYPE=bot`.
- `docker-entrypoint.sh` ya ramifica `bot` → `python -m apps.bot.main` (escucha en `PORT`).
- El bot expone el webhook `POST /tg/{slug}`: necesita ser **público** (Railway le da dominio). Valida
  `X-Telegram-Bot-Api-Secret-Token` contra el secret cifrado del control DB y dedup por Redis.
- Healthcheck: el proceso responde HTTP en `PORT`; usa `/health` si está, o el TCP del puerto.

### 10.2 Migraciones y datos

1. **[CLI]** Migraciones (incluye control `0010_gmail_cuentas`):
   ```bash
   alembic -c migrations/control/alembic.ini upgrade head
   python -m tools.migrate_tenants
   ```
2. **ETL** de FerreBot → tenant (en-red, **URL directa** para el `setval`, no PgBouncer):
   ```bash
   python -m tools.etl_puntorojo --origen-url <dump_legacy_restaurado> --slug puntorojo
   python -m tools.etl_puntorojo.verify --origen-url <dump_legacy_restaurado> --slug puntorojo   # gate: exit 0
   ```
   El `verify` (paridad de conteos, sumas, **continuidad DIAN**, FKs, fechas Colombia y stock↔kardex)
   es **gate de corte**: no se corta si no da exit 0.

### 10.3 ⚠️ Rotación de secretos ANTES del corte (no negociable)

El manifiesto `tools/onboarding/puntorojo.json` está en `.gitignore` (**no** llegó a git — la copia
local sí tenía token de Telegram y password de MATIAS en claro; se scrubbearon a placeholders). Como
esos valores reales existieron fuera de cifrado, antes de mover tráfico real trátalos como comprometidos:

1. **BotFather** → `/revoke` del bot → toma el token NUEVO.
2. Cambia el **password de MATIAS** en el portal.
3. Carga los secretos NUEVOS **cifrados** vía el provisionador (`tools/provision_from_manifest.py` con el
   manifiesto corregido) o directo en `secretos_empresa`.
4. **Saca los valores reales del JSON versionado** (déjalo con placeholders) y commitea.

### 10.4 Encender contabilidad (opt-in, tras el ETL)

```bash
python -m tools.set_feature puntorojo contabilidad_ledger on
# admin, una vez, sobre el tenant migrado:
#   POST /api/v1/contabilidad/puc/sembrar      → siembra el PUC
#   POST /api/v1/contabilidad/backfill         → proyecta asientos de lo migrado
#   POST /api/v1/contabilidad/apertura         → asiento de apertura con los saldos (caja/fiados/inventario/IVA)
```
El tab **Estados Financieros** aparece solo con el flag `contabilidad_ledger` encendido.

### 10.5 Bancolombia (Gmail push)

1. **Env vars de plataforma** (un solo proyecto GCP, todos los tenants): `GMAIL_CLIENT_ID`,
   `GMAIL_CLIENT_SECRET`.
2. Registrar el buzón del tenant (cifra el refresh_token + da de alta `gmail_cuentas`):
   ```bash
   python -m tools.set_gmail_token puntorojo --refresh-token <REFRESH> \
       --email ferreteria.bancolombia@gmail.com \
       --pubsub-topic projects/<PROJ>/topics/bancolombia-notif
   # imprime el webhook_token → configura la subscription de Pub/Sub con push endpoint:
   #   https://<host-api>/webhooks/bancolombia/<webhook_token>
   ```
3. El cron `renovar_watch_gmail` (worker, 08:30 UTC) activa/renueva el watch de Gmail; para arrancar ya,
   dispara una renovación manual encolando el job o esperando la primera corrida.
4. Gate por feature: `python -m tools.set_feature puntorojo conciliacion_bancaria on`.

### 10.6 Corte (lo ejecuta el OWNER; ventana de bajo tráfico)

Requisitos previos: §10.1–10.5 hechos, `verify.py` verde, y **acierto del bot re-medido ≥ baseline con
0 peligrosos** (`tests/evals/replay/replay.py` contra el tenant migrado, gate de `migracion-puntorojo.md §10`).

- **Telegram** (checklist completo en `migracion-puntorojo.md §10`): `deleteWebhook` en el bot viejo →
  `setWebhook` del MISMO token (el ROTADO en §10.3) a `https://<host-bot>/tg/puntorojo` con
  `secret_token=<el cifrado en control DB>` → `getWebhookInfo` limpio → smoke "1 vinilo" + reenvío del
  mismo update (dedup Redis no duplica). **Rollback** posible mientras no se emitan facturas DIAN nuevas.
- **Bancolombia**: re-apunta la **MISMA** subscription de Pub/Sub al endpoint nuevo
  (`gcloud pubsub subscriptions update <sub> --push-endpoint=https://<host-api>/webhooks/bancolombia/<token>`).
  **Nunca crees una segunda subscription** — dos activas notificarían dos veces (la dedup por
  `gmail_message_id` vive en bases distintas). Corre `renovar_watch` una vez tras el corte.
- **Post-corte**: monitorea 24–48 h (5xx, p95, % bypass vs modelo, `watch_expira` en `gmail_cuentas`).

### 10.7 Riesgos del corte

- **G4 (zona de timestamps)**: el ETL parametriza `--tz-origen`; la muestra confirmó que el servidor
  legacy escribía `created_at` en UTC y `fecha`+`hora` en hora Colombia (ya cableado). `verify.py` re-chequea.
- **Doble notificación Bancolombia**: re-apuntar la subscription existente, jamás duplicarla.
- **Watch de Gmail (7 días)**: el cron renueva anticipadamente (<48h) y alerta a Telegram si el refresh
  está revocado.
- **Consecutivos DIAN**: el ETL arranca `fe_factura_consecutivo_seq` desde el consecutivo embebido en
  `numero` (no la PK-seq); `verify.py` lo comprueba y el smoke de factura sandbox lo confirma.
