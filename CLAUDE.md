# FerreBot SaaS — POS multi-empresa

POS para ferreterías y comercios con **dashboard web** + **agente IA en Telegram**, construido como **SaaS multi-empresa** (una empresa = un *tenant*). Punto Rojo es el tenant #1.

> Este archivo contiene solo lo esencial. El plan completo y los detalles viven en `docs/`.
> **Empieza por `docs/architecture.md`.**

## Stack

- **Backend:** Python 3.11+, FastAPI, SQLAlchemy + Alembic
- **Base de datos:** PostgreSQL — **una base por empresa** (DB-per-tenant) + un *control DB* global
- **Frontend:** React + Vite (white-label por empresa; tema por defecto #C8200E)
- **Bot:** python-telegram-bot (webhook), un bot por empresa
- **IA:** Claude + OpenAI — híbrido: bypass Python (~60% de ventas sin IA) + function calling
- **Infra:** Railway · PgBouncer (pooling de conexiones) · Redis (cola/caché)

## Reglas no negociables

1. **Aislamiento de empresa primero.** Ninguna consulta sin resolver el tenant. Nunca cruzar datos entre empresas: la conexión ya apunta a la base de esa empresa.
2. **Acceso a datos solo por la capa de repositorios.** Nada de SQL suelto en routers ni servicios.
3. **`async`/`await`** en cualquier endpoint que emita eventos en tiempo real.
4. **Zona horaria Colombia (UTC-5)** siempre, backend y frontend. Nunca `date.today()` crudo.
5. **Secretos jamás en el código ni en git.** Las credenciales por empresa (MATIAS, Cloudinary, token de bot) van **cifradas** en el control DB. Ver `SECURITY.md`.
6. **Logging estructurado** con `tenant_id` y `request_id`. Nunca `print`.
7. **Nada modifica stock sin movimiento de inventario, ni caja sin movimiento de caja.**
8. **Idempotencia** en operaciones críticas (venta, emisión de factura, webhooks de pago).

## Comandos

```bash
# API local
uvicorn apps.api.main:app --reload --port 8000
# Bot local
python -m apps.bot.main
# Tests
pytest
# Migraciones (control DB y por-tenant) — ver docs/runbook.md
alembic -c migrations/control/alembic.ini upgrade head
python -m tools.migrate_tenants            # aplica a todas las empresas
```

## Dónde está cada cosa

| Necesitas… | Archivo |
|---|---|
| El plan / arquitectura completa | `docs/architecture.md` |
| Por qué se decidió algo | `docs/adr/` |
| Diagramas (arquitectura, ER, secuencias) | `docs/diagrams.md` |
| Modelo de datos (control + por-empresa) | `docs/data-model.md` |
| Esquema detallado (tablas, tipos, FKs) | `docs/schema.md` |
| Contrato de API (endpoints v1) | `docs/api-contract.md` |
| Herramientas IA + spec del bypass | `docs/ai-tools.md` |
| Lógica de FerreBot a portar (notas) | `docs/ferrebot-logica-portar.md` |
| MATIAS API / facturación (extracción) | `docs/facturacion-matias-extract.md` |
| Topología de infra (Railway) | `docs/infra-railway.md` |
| Migración de FerreBot a Punto Rojo | `docs/migracion-puntorojo.md` |
| Decisiones de migración + spec ETL | `docs/decisiones-migracion.md` |
| Multi-tenancy a fondo (conexiones, migraciones) | `docs/tenancy.md` |
| Capacidades por empresa (feature flags) | `docs/feature-flags.md` |
| Auth y permisos (RBAC) | `docs/auth-rbac.md` |
| Secretos por empresa | `docs/secrets.md` |
| Modo offline (PWA) | `docs/offline-sync.md` |
| Facturación DIAN (estados, reintentos) | `docs/facturacion-dian.md` |
| Operar (provisioning, migraciones, restore) | `docs/runbook.md` |
| Dar de alta una empresa | `docs/onboarding-tenant.md` |
| Plantillas de manifiesto por vertical (peluquería, etc.) | `docs/plantillas-verticales.md` |
| Manifiesto de tenant + provisionador de un paso | `docs/adr/0007-manifiesto-tenant-y-provisionador.md` · `tools/provision_from_manifest.py` |
| Superficie pública Melquiadez (landing, sign-in, demos, switch Kapso) | `docs/plan-melquiadez-superficie-publica.md` |
| Reglas de desarrollo | `.claude/rules/` |

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`gh` CLI), repo `andres05tmm/Ferrebot-Saas`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: `CONTEXT.md` (if present) + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
