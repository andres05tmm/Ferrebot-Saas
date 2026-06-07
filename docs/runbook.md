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
# Fija la versión del cliente con Docker para evitar el choque pg_dump < servidor:
export PG_DUMP="docker run --rm postgres:17 pg_dump"
python -m tools.backup_db
# → backups/<YYYYMMDDTHHMMSSZ>/railway.dump + ferrebot_<slug>.dump  (imprime tamaños)
```
Si sale `aborting because of server version mismatch` o `pg_dump: command not found`, usa la opción
Docker de arriba (no necesita montar volúmenes: el dump viaja por stdout).

### Probar la restauración (un backup no probado no es un backup)
```bash
# Base scratch: un Postgres de pruebas (p. ej. el Docker local) con una DB vacía 'scratch_verify'.
export PG_RESTORE="docker run --rm -i postgres:17 pg_restore"   # -i: lee el .dump por stdin
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

### Restaurar una empresa a producción
Restaurar `ferrebot_<slug>.dump` sobre su base (o una nueva) sin afectar a las demás (DB-per-tenant).
Verificar primero en scratch; luego `pg_restore --clean --if-exists -d <url-del-tenant>`.

## Conexiones (PgBouncer)
- Toda conexión pasa por PgBouncer. Si aparece "too many connections": revisar tope de pool por empresa, evicción de engines inactivos y límites de PgBouncer.

## Emisión DIAN
- Asíncrona (ARQ) con reintentos y dead-letter. Reconciliar estados pendientes con un job periódico.

## Salud
- `/health` y `/ready`; monitor de uptime externo (no self-ping).
