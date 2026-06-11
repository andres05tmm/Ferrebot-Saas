# Handoff Cowork — Deploy + Bot + Backups (7 jun 2026)

> Léelo al retomar en una sesión nueva de Cowork. Rol: Cowork = copiloto de arquitectura/deploy;
> Andrés ejecuta en Claude Code y en Railway. Cowork revisa leyendo, no ejecuta git en el repo.

## Lo que se hizo esta sesión

- **Deploy de los 3 servicios en Railway (lean):** `api` (ya estaba) + **`worker`** + **`bot`** nuevos.
  Misma imagen, difieren por `SERVICE_TYPE`. Bot con webhook `POST /tg/puntorojo` + secret-token.
- **`telegram_webhook_secret` de PR:** no se provisionaba. Se agregó a `_CLAVES_SECRETAS`
  (`tools/provision_tenant.py`) + a `puntorojo.json` + re-provisión. Valor cifrado en el control DB.
- **Deps de runtime:** se agregaron `httpx`, `openai`, `anthropic` a `[project.dependencies]`
  (faltaban; el bot crasheaba con `ModuleNotFoundError`).
- **LLM del bot = Claude.** Variables en el servicio `bot`: `LLM_PROVIDER=claude`,
  `LLM_MODEL_WORKER=claude-haiku-4-5-20251001`, `LLM_MODEL_ORQUESTADOR=claude-sonnet-4-6`.
  `OPENAI_API_KEY` queda **solo para Whisper** (audio), no para el chat.
- **Mejoras del bot:** texto plano sin Markdown; `consultar_producto` expone unidad + fracciones (sin
  stock en el texto); regla de prompt "nunca calcular fracciones, siempre consultar"; búsqueda por
  nombre base conservando calificadores (t1, color).
- **Bypass conectado** al flujo del bot (antes solo vivía en tests). Ventas simples deterministas, sin
  LLM. Resuelve unidades ("2 galones de thinner" → quita "galones de" → producto "thinner").
- **Botones (inline keyboards + callbacks):** confirmación de venta con
  `[Efectivo][Transferencia][Datáfono]` / `[Cancelar]`. Estado pendiente en Redis por tenant.
  Métodos = `efectivo/transferencia/datafono/fiado` (se quitaron tarjeta/nequi/daviplata del `Literal`;
  el enum de Postgres se dejó intacto + `datafono` vía migración tenant **0007**).
- **Datos de PR verificados** (contra prod): **632 productos, 722 fracciones**, unidades reales
  (Galón/Mts/Kg/Cms). El catálogo SÍ trae fracciones/unidades — el hueco era de exposición, no de dato.
- **Backups de producción:** `tools/backup_db.py` (pg_dump del control DB + cada tenant) + **restore
  PROBADO** (restaurado a scratch, conteos 632/722/5 ✓). `.env.prod` (gitignored) + sección DR en runbook.

## Estado de producción

- Railway: Postgres (**PG 18.4**), Redis, `api`, `bot`, `worker` — todos vivos. PR operando.
- Dashboard + bot con botones funcionando. Migración **0007 (datafono)** aplicada a PR prod.
- Existe un backup probado en `backups/<timestamp>/` (control + tenant), en el PC de Andrés.

## Gotchas operativos CRÍTICOS (leer antes de tocar producción)

1. **El `.env` del repo apunta a `localhost:5433` (DEV).** Para ops contra **PRODUCCIÓN** usar
   **`.env.prod`** (gitignored) con las **URLs PÚBLICAS** de Railway:
   `...@<host>.proxy.rlwy.net:<PUERTO>/...` — **NO** `railway.internal` (privada, solo dentro de Railway)
   ni `localhost`. El host/puerto público sale de `DATABASE_PUBLIC_URL` (Railway → Postgres → Variables).
2. **Tools de ops contra prod** usan `cargar_env_prod()` (`tools/_prodenv.py`, lee `.env.prod`).
   `migrate_tenants` aún se corre seteando las env vars de prod a mano en PowerShell (override del `.env`).
   → Pendiente: un `/migrate-prod` o wrapper que use `.env.prod` siempre (tarea de ops).
3. **`pg_dump` debe ser ≥ versión del servidor (PG 18.4)** → usar `docker run --rm postgres:18 pg_dump`.
   Para restore: `docker run --rm -i postgres:18 pg_restore` (lee el .dump por stdin).
4. **`SECRETS_MASTER_KEY` de Railway: NO cambiarla** (descifra los secretos de PR).
5. **Cerrar la terminal tras correr ops contra prod** (las env vars de prod quedan vivas en la sesión).

## Pendiente (con prioridad)

**Red de seguridad (en curso — lo más importante):**
- Off-site del `.dump` (sacarlo del PC → carpeta sincronizada a la nube).
- Automatizar el backup (Task Scheduler diario, o cron en Railway — ojo: la imagen runtime no trae pg_dump).
- Sentry cableado en `api`/`bot`/`worker` (hoy solo declarado en settings, no inicializado).
- Monitor de uptime externo sobre `/health`.

**No-bot (frentes del proyecto):**
- Migración real de PR (Fase 15): clientes/aliases, **conteo físico de inventario** + indicador de stock
  negativo (631 en 0), continuidad de consecutivos DIAN, validación de paridad, corte del viejo.
- Dashboard: **Ventas Rápidas con fracciones/unidades** (hoy solo vende unidades normales); panel
  super-admin de onboarding; **flujo de caja** (feature nueva — cuadre → reporte → proyección).
- Facturación: asíncrono DIAN (necesita **sandbox MATIAS**); resto fiscal.
- SaaS comercial (Fase 16): billing, 2ª empresa.

**Bot — sesión intensiva dedicada (decisión de Andrés):**
- **BUG de precio:** lija por cm → "20 cm" se cobró **$400.000** (debe ser **$4.000**). Falta modelar
  "precio por N unidades" en el motor de precios / datos. El viejo lo hacía (÷100).
- Hacerlo más inteligente que el viejo pero escalable: slot-filling, desambiguación con BOTONES (no
  re-teclear), reglas de precio en datos/motor (no en prompt), aliases que crecen, evals + observabilidad,
  pulir botones (emojis, "Modificar venta").

## Cómo retomar

Leer este doc + `CLAUDE.md` + `.claude/rules/`. Sugerencia de Cowork: cerrar la **red de seguridad**
(off-site + automático) o arrancar un frente no-bot visible (**Ventas Rápidas con fracciones** o **flujo
de caja**), y dejar el bot para la sesión intensiva dedicada.
