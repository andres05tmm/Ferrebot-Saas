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
- Backups por empresa + PITR en la instancia de Postgres.
- Restaurar una empresa: recuperar su base a un punto en el tiempo sin afectar a las demás.
- **Probar la restauración** periódicamente (un backup no probado no es un backup).
- No borrar histórico fiscal DIAN (retención ~5 años).

## Conexiones (PgBouncer)
- Toda conexión pasa por PgBouncer. Si aparece "too many connections": revisar tope de pool por empresa, evicción de engines inactivos y límites de PgBouncer.

## Emisión DIAN
- Asíncrona (ARQ) con reintentos y dead-letter. Reconciliar estados pendientes con un job periódico.

## Salud
- `/health` y `/ready`; monitor de uptime externo (no self-ping).
