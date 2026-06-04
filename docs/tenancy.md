# Multi-tenancy a fondo (DB por empresa)

> Cómo se resuelve la empresa, cómo se enruta a su base y cómo se migran/aprovisionan N bases. Esquema en `schema.md`; reglas resumidas en `.claude/rules/multitenancy.md`.

## Modelo

- **Control DB:** una base global con `empresas`, `tenant_databases`, `planes`, `secretos_empresa`, `branding`, `super_admins`.
- **App DB por empresa:** una base por tenant con todo el esquema de negocio. La base ES la frontera; no hay `empresa_id` en las tablas.
- **PgBouncer** delante de todo Postgres.

## 1. Resolución de la empresa

Orden de resolución en cada request:

1. **Dashboard / API:** subdominio `empresa.BASE_DOMAIN` → `slug`. Si no hay subdominio, el claim `tenant_id` del JWT.
2. **Bot:** la ruta del webhook `/tg/{slug}` identifica la empresa (cada empresa tiene su token de bot).
3. **Jobs/cron:** reciben `tenant_id` explícito (nunca asumen "el actual").

El `slug`/`tenant_id` se valida contra el control DB. Si no resuelve a una empresa **activa**, se responde 404/403 y **no se toca ninguna base**.

## 2. Middleware de tenant

```
TenantMiddleware:
  tenant = resolver(request)                 # subdominio | jwt | ruta bot
  empresa = control_cache.get(tenant)        # cache con TTL (ver §3)
  if not empresa or empresa.estado != activa: -> 403/404
  request.state.tenant = empresa             # disponible para deps y logging
```

El `tenant_id` entra en **todos los logs** (`request_id` + `tenant_id`).

## 3. Caché del control plane

- `control_cache: slug -> { empresa_id, db_conn, estado, branding }` con **TTL corto** (p. ej. 60 s) e invalidación al cambiar estado/branding desde `/admin`.
- Evita pegarle al control DB en cada request. La conexión de la empresa (`db_conn`) se descifra una vez y se mantiene en memoria del proceso.

## 4. Enrutamiento de conexiones (engine cache)

- `engine_cache: tenant_id -> Engine` (SQLAlchemy), creado **perezosamente** en el primer uso.
- Pool por empresa **pequeño**: `pool_size=2, max_overflow=2` (porque PgBouncer hace el trabajo pesado de multiplexar).
- **Evicción LRU**: tope de N engines vivos (p. ej. 200); se cierran los de empresas inactivas. Empresas que vuelven recrean su engine.
- Dependencia FastAPI:

```python
def get_tenant_db(request) -> Session:
    engine = engine_cache.get_or_create(request.state.tenant.id)
    session = Session(bind=engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback(); raise
    finally:
        session.close()
```

**Regla:** la sesión queda atada a la empresa por todo el request. Nunca se cambia de tenant a mitad de flujo; nunca se cachea un objeto de una empresa para otra.

## 5. PgBouncer (clave del modelo)

- Modo de pool: **transaction**. Permite muchísimas conexiones de app sobre pocas reales de Postgres.
- **Presupuesto de conexiones:** Postgres `max_connections` (ej. 100) se reparte vía PgBouncer (`default_pool_size`, `max_db_connections`). Con transaction pooling, cientos de empresas comparten ~unas decenas de conexiones reales.
- **Caveat importante — LISTEN/NOTIFY:** el `LISTEN` del SSE necesita una **conexión de sesión persistente**, que NO funciona sobre PgBouncer en modo transaction. Por eso:
  - El acceso de negocio (CRUD) va por **PgBouncer** (transaction).
  - El **listener de pg_notify** de cada empresa abre una **conexión directa a Postgres** (o por un PgBouncer en modo session dedicado), una por empresa con SSE activo.
- Sin prepared statements del lado servidor en transaction mode (usar `prepare_threshold=None` en el driver).

## 6. Tiempo real por empresa

- Cada app DB emite `pg_notify('ferrebot_events', payload)` en su propia base.
- La API mantiene **un listener por empresa con suscriptores SSE activos** (conexión directa, ver §5). Sin suscriptores, no hay listener.
- El stream `GET /events` solo recibe eventos de la empresa del request.

## 7. Migraciones multi-base (Alembic)

Dos árboles independientes:

```
migrations/
├── control/   # esquema del control DB
└── tenant/    # esquema de negocio (se aplica a TODAS las empresas)
```

- `migrations/tenant/env.py` recibe la conexión de la empresa por parámetro (no usa una URL fija).
- **Runner** `tools/migrate_tenants`:
  1. Lee `empresas` del control DB (estado activa/suspendida).
  2. Por cada empresa: `alembic upgrade head` sobre su base, en transacción.
  3. Registra la versión aplicada; si una falla, **continúa con las demás y reporta** (no aborta todo).
  4. Idealmente corre como **job ARQ** en el deploy, no en el request.
- **Cero downtime:** migraciones **backward-compatible** (agregar antes de usar; no romper columnas en uso). Cambios destructivos en dos pasos (deploy que tolera ambos → migración → deploy que limpia).

## 8. Aprovisionamiento de una empresa

`tools/provision_tenant(slug, nombre, nit, plan)`:

1. `CREATE DATABASE` para la empresa (conexión admin).
2. Registrar en `tenant_databases` (URL cifrada).
3. `alembic upgrade head` (árbol tenant) sobre la nueva base.
4. Sembrar datos base (categorías, métodos de pago, `config_empresa`).
5. Guardar secretos cifrados (MATIAS, Cloudinary, token de bot) y branding.
6. Crear el usuario admin de la empresa.
7. Registrar webhook `/tg/{slug}` del bot.
8. Marcar `empresas.estado = activa`.

Todo el flujo es **idempotente** y corre como job (puede reintentarse). Detalle operativo en `runbook.md`; alta paso a paso en `onboarding-tenant.md`.

## 9. Seguridad del aislamiento

- Ninguna consulta sin `request.state.tenant` resuelto.
- La sesión está atada a la base de la empresa; un bug de "tenant equivocado" es imposible si siempre se usa `get_tenant_db()`.
- Secretos de la empresa se descifran solo en memoria, por request, con `SECRETS_MASTER_KEY` (ver `SECURITY.md`).
- Test obligatorio: la empresa A nunca ve datos de la empresa B (ver `.claude/rules/testing.md`).

## 10. Secuencia de un request (venta desde el dashboard)

```
1. Browser  →  POST empresa1.app.com/api/v1/ventas  (JWT, Idempotency-Key)
2. TenantMiddleware: slug=empresa1 → control_cache → empresa activa
3. Auth: valida JWT (tenant_id coincide con empresa1), rol vendedor
4. get_tenant_db(): engine de empresa1 (vía PgBouncer) → Session
5. Servicio de ventas: valida stock, calcula totales, inserta venta + detalle
   + movimientos_inventario (misma transacción), respeta Idempotency-Key
6. commit → pg_notify('ferrebot_events', {venta_registrada})
7. Listener de empresa1 → SSE → dashboards de empresa1 se actualizan
```
