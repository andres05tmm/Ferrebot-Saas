# Task list (arranque)

## Cimientos
- [ ] Esqueleto de repo (apps/core/modules/migrations/dashboard)
- [ ] core/tenancy: resolución de empresa + router de conexiones (PgBouncer)
- [ ] core/db: engines y sesiones por empresa; core/config; core/auth (JWT/RBAC)
- [ ] Alembic: árboles control/ y tenant/; runner migrate_tenants
- [ ] tools/provision_tenant

## Dominio (portar de FerreBot)
- [ ] Modelos + repositorios + servicios por dominio
- [ ] bypass y ai/tools.py
- [ ] Servicio MATIAS (caché _get_city_id) + DS-NO + notas + eventos DIAN
- [ ] SSE por empresa

## Frontend
- [ ] Dashboard React (white-label) como PWA con cola offline
- [ ] Idempotencia en venta/emisión

## Migración Punto Rojo
- [ ] Provisionar tenant #1 y copiar datos de referencia + histórico DIAN
- [ ] Tests de paridad; corte de webhooks
