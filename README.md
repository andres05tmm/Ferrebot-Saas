# Melquiadez

> El repositorio se llama `ferrebot-saas` por su origen; la plataforma es **Melquiadez**.

Plataforma SaaS multi-empresa que le da a un negocio de barrio un empleado que no duerme: un **agente IA**
que atiende su WhatsApp/Telegram (agenda citas, toma pedidos, reserva, cobra) + un **dashboard web** (React)
donde el dueño lo ve todo, con POS y facturación electrónica DIAN. Cada empresa es un *tenant* con su propia
base de datos.

## Estado

En arranque. **Punto Rojo** (ferretería; migración desde el proyecto **FerreBot**, que sigue siendo el nombre
de su producto) es el tenant #1. La superficie pública (landing, sign-in, demos por vertical) vive en
`docs/plan-melquiadez-superficie-publica.md`.

## Stack

Python 3.11 · FastAPI · SQLAlchemy + Alembic · PostgreSQL (una base por empresa) · React + Vite · python-telegram-bot · Claude/OpenAI · MATIAS (DIAN) · Railway.

## Estructura

```
apps/        api (FastAPI) y bot (Telegram)
core/        kernel compartido (tenancy, db, config, auth, events)
modules/     dominios (ventas, inventario, caja, facturacion, ...)
ai/          prompts, tools, bypass, voz, RAG
migrations/  control/ y tenant/ (Alembic)
dashboard/   React + Vite (white-label)
docs/        plan, ADRs, modelo de datos, runbook
.claude/     reglas para el asistente
```

## Correr en local

```bash
cp .env.example .env     # completa las variables
uvicorn apps.api.main:app --reload   # API + dashboard
python -m apps.bot.main              # bot
pytest                               # tests
```

## Documentación

Arranca por **`docs/architecture.md`**. Decisiones clave en `docs/adr/`. Operación en `docs/runbook.md`.
