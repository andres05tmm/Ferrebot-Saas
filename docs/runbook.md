# Runbook (operación)

## Provisionar una empresa
1. `python -m tools.provision_tenant --nombre "X" --nit ... --slug x`
   - crea la base, corre `migrations/tenant` (upgrade head), siembra datos base.
2. Cargar secretos cifrados (MATIAS, Cloudinary, token de bot) y branding.
3. Crear admin de la empresa y asignar subdominio.
Ver `onboarding-tenant.md` para el paso a paso.

## Aplicar una migración a todas las empresas
1. Crear la revisión en `migrations/tenant`.
2. `python -m tools.migrate_tenants` (itera empresas; idealmente como job ARQ).
3. Verificar versión por empresa; migraciones backward-compatible para cero downtime.

## Backups y restauración (DR)

El plan de Railway no trae backups nativos, así que los generamos con `tools/backup_db.py`
(`pg_dump -Fc` del control DB + cada tenant). Es **solo lectura** sobre prod.

### Preparar el entorno de ops (una vez)
```bash
cp .env.prod.example .env.prod        # NO se commitea (.gitignore lo ignora)
# Editar .env.prod con las URLs PÚBLICAS de Railway (Connect → Public Network) y la
# SECRETS_MASTER_KEY de prod (la misma con la que se cifraron los secretos).
```

### Hacer el backup
```bash
# Fija la versión del cliente con Docker para evitar el choque pg_dump < servidor (PG 18.x):
export PG_DUMP="docker run --rm postgres:18 pg_dump"
python -m tools.backup_db
# → backups/<YYYYMMDDTHHMMSSZ>/railway.dump + ferrebot_<slug>.dump  (imprime tamaños)
```
Si sale `aborting because of server version mismatch` o `pg_dump: command not found`, usa la opción
Docker de arriba (no necesita montar volúmenes: el dump viaja por stdout).

### Probar la restauración (un backup no probado no es un backup)
```bash
# Base scratch: un Postgres de pruebas (p. ej. el Docker local) con una DB vacía 'scratch_verify'.
export PG_RESTORE="docker run --rm -i postgres:18 pg_restore"   # -i: lee el .dump por stdin
python -m tools.backup_db --verify backups/<ts>/ferrebot_puntorojo.dump \
  --scratch postgresql://postgres:ferrebot@localhost:5433/scratch_verify
# Restaura y cuenta tablas clave (productos, ventas, usuarios, …). Conteos > 0 = el backup sirve.
```
El restore-verify NO toca prod (va contra la scratch explícita; no carga `.env.prod`).

### Retención y off-site
- El `.dump` lleva **TODO**, incluidos los secretos por empresa cifrados (`secretos_empresa`). Aunque
  estén cifrados, **trátalo como secreto**: guárdalo fuera de git y fuera de Railway (almacenamiento
  off-site cifrado, p. ej. un bucket con acceso restringido). Sin la `SECRETS_MASTER_KEY` el control
  DB restaurado no sirve: respáldala aparte (gestor de secretos), nunca junto al dump.
- Retención sugerida: diarios 7 días, semanales 4–8 semanas. Probar un restore al menos al cambiar el
  esquema.
- No borrar histórico fiscal DIAN (retención ~5 años).

### Backup automático (Windows, semanal/opcional)

**Apagado por defecto.** El backup automático solo corre si lo activas explícitamente: pon
`BACKUP_ENABLED=on` en `.env.prod`. Mientras esté en `off`, el wrapper corre pero `tools/backup_db.py`
imprime `backup deshabilitado (BACKUP_ENABLED=off)` y termina con éxito (no alarma al scheduler).

**Requisitos:** Docker Desktop corriendo · `.env.prod` en la raíz del repo (URLs públicas de Railway +
`SECRETS_MASTER_KEY`) · el PC encendido y con sesión iniciada a la hora programada.

**Wrapper:** `tools/backup_daily.ps1` (committeable, sin secretos) hace `cd` a la raíz, verifica Docker,
fija el cliente `postgres:18` (servidor PG 18.x) y ejecuta `python -m tools.backup_db --podar 8`
(respaldo + retención de 8 semanas). Loguea a `backups\logs\backup_<timestamp>.log` y propaga el código
de salida.

**Alta en Task Scheduler (una sola vez)** — usuario logueado, SEMANAL los domingos 20:00. Ajusta
`C:\ruta\al\repo` a la ruta real del repo:

```bat
schtasks /Create /TN "FerreBot Backup Prod" /SC WEEKLY /D SUN /ST 20:00 ^
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\ruta\al\repo\tools\backup_daily.ps1"
```

**Verificar que corrió:**
- Carpeta nueva `backups\<YYYYMMDDTHHMMSSZ>\` con los `.dump` (control + tenants).
- Último `backups\logs\*.log` sin errores y con código de salida 0.
- Restore-verify manual de algún dump (la prueba de que sirve):
  ```bash
  python -m tools.backup_db --verify backups\<ts>\ferrebot_puntorojo.dump ^
    --scratch postgresql://postgres:ferrebot@localhost:5433/scratch_verify
  ```

**Nota:** el off-site (copiar los dumps a la nube) es un paso aparte, **pendiente**. Por ahora los
respaldos quedan solo en el disco local (trátalos como secreto: ver "Retención y off-site" arriba).

### Restaurar una empresa a producción
Restaurar `ferrebot_<slug>.dump` sobre su base (o una nueva) sin afectar a las demás (DB-per-tenant).
Verificar primero en scratch; luego `pg_restore --clean --if-exists -d <url-del-tenant>`.

## Conexiones (PgBouncer)
- Toda conexión pasa por PgBouncer. Si aparece "too many connections": revisar tope de pool por empresa, evicción de engines inactivos y límites de PgBouncer.

## Emisión DIAN
- Asíncrona (ARQ) con reintentos y dead-letter. Reconciliar estados pendientes con un job periódico.

## Salud
- `/health` y `/ready`; monitor de uptime externo (no self-ping).

## Monitoreo de uptime (externo)

Un servicio externo hace ping periódico a la **api** y al **bot**; si dejan de responder, avisa por
email. No se hace self-ping desde la propia app (un proceso caído no puede alertar de su caída).

### Qué se monitorea
- **GET `<URL_API>/health`** — la URL pública de Railway del servicio api.
- **GET `<URL_BOT>/health`** — la URL pública de Railway del servicio bot.

`/health` es público (no exige tenant ni JWT: está en `TenantMiddleware._PUBLIC_PATHS`) y estático
(no toca dependencias). Configuración del monitor en ambos:
- Espera **HTTP 200** con body `{"status":"ok"}`.
- **Keyword `ok`** en el cuerpo (no basta el 200: valida que respondió la app, no un proxy).
- **Intervalo: 5 min.**

> Para un chequeo más profundo (control DB + Redis) está **`<URL_API>/ready`** (200 si todo OK, 503 si
> algo falla). Útil al diagnosticar una alerta; como monitor continuo se prefiere `/health` (barato y
> sin tocar dependencias en cada ping).

### El worker no tiene HTTP (gap conocido)
El **worker** (ARQ) no expone endpoint, así que **no hay monitor de ping**. Su salud se cubre con:
- **Sentry** — captura errores de los jobs (emisión DIAN), ver "Backups"/observabilidad.
- **Reinicio automático de Railway** — si el proceso muere, Railway lo relanza.

Limitación: un worker "vivo pero atascado" (sin crashear ni emitir errores) no dispara ninguna alarma.
Pendiente: una señal de liveness del worker (p. ej. heartbeat a Redis vigilado por un monitor).

### Proveedor sugerido
**UptimeRobot** (plan free: 50 monitores, intervalo 5 min). Crear **2 monitores HTTP(s)** (api y bot)
apuntando a cada `/health`, con keyword `ok` y alertas al **email del equipo**. Cualquier proveedor
equivalente (Better Stack, Healthchecks, Pingdom) sirve igual.

### Ante una alerta
1. **Railway** — logs del servicio caído (api o bot): ¿crash, OOM, error de arranque, deploy en curso?
2. **`<URL_API>/ready`** — chequea control DB + Redis; un 503 acota el fallo a una dependencia.
3. **Sentry** — excepciones recientes (incluye los errores de jobs del worker).
