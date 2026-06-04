# Diseño de sistema

## Estructura de carpetas (propuesta)

```
repo/
├── apps/
│   ├── api/        # FastAPI: main, routers montados, estáticos del dashboard
│   └── bot/        # Telegram: webhook por empresa, handlers
├── core/           # kernel compartido
│   ├── tenancy/    # resolución de empresa, router de conexiones (PgBouncer)
│   ├── db/         # engines, sesiones, Base de SQLAlchemy
│   ├── config/     # settings, secretos, zona horaria Colombia
│   ├── auth/       # JWT, RBAC, permisos
│   └── events/     # pg_notify, SSE
├── modules/
│   └── <dominio>/  # router.py · service.py · repository.py · models.py · schemas.py · tests/
├── ai/             # prompts, tools, bypass, voz, RAG
├── migrations/
│   ├── control/    # Alembic del control DB
│   └── tenant/     # Alembic del esquema de empresa
├── tools/          # scripts: provisionar empresa, migrar todas las empresas
└── dashboard/      # React + Vite (PWA, white-label)
```

## Capas y dependencias

`router -> service -> repository -> modelo`. El dominio (service) no importa infraestructura. La resolución de empresa ocurre en el router/middleware y se inyecta la sesión del tenant.

## API

- Versionado: `/api/v1/...`.
- Auth: JWT; dependencia `get_current_user`; resolución de empresa por subdominio o claim.
- Idempotencia: header `Idempotency-Key` en venta, emisión de factura y webhooks.
- Tiempo real: `GET /events` (SSE) acotado a la empresa.
- Salud: `GET /health`, `GET /ready`.

## Concurrencia

- Caché de engines por empresa con límite y evicción; todo a través de PgBouncer.
- Jobs pesados a la cola (ARQ); nunca bloquear el request de emisión DIAN.
