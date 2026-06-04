# Multi-tenancy (regla crítica)

Modelo: **una base de datos por empresa** (DB-per-tenant) + un *control DB* global. La base ES la frontera del tenant.

## Reglas

1. **Nunca consultar sin resolver la empresa.** Todo request resuelve el tenant (por subdominio o por el JWT) antes de tocar datos. Si no hay tenant resuelto, no hay consulta.
2. **Una conexión por empresa.** El acceso a datos usa la sesión/engine de esa empresa (dependencia `get_tenant_db()`). Jamás mezclar sesiones de dos empresas en un mismo flujo.
3. **El control DB es aparte.** Empresas, planes, branding y secretos viven en el control DB; los datos de negocio en la app DB de cada empresa. No mezclar modelos de ambos planos.
4. **Las tablas de negocio NO llevan `empresa_id`.** El aislamiento lo da la base, no una columna.
5. **Secretos por empresa cifrados** en el control DB. Ver `security.md`.
6. **Migraciones en dos árboles:** `migrations/control/` y `migrations/tenant/`. Una migración del esquema de negocio debe aplicarse a TODAS las empresas (runner). Ver `docs/runbook.md`.
7. **Provisionar = automatizado:** crear base → migrar → sembrar → secretos → admin. Ver `docs/onboarding-tenant.md`.
8. **Conexiones:** ir siempre a través de PgBouncer; respetar límites de pool por empresa y el tope global.

## Checklist al crear un endpoint o job

- [ ] ¿Resuelve la empresa antes de consultar?
- [ ] ¿Usa la sesión del tenant (no una global)?
- [ ] ¿El evento en tiempo real está acotado a esa empresa?
