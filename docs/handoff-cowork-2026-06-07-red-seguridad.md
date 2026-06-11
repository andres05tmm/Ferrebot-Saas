# Handoff Cowork — Red de seguridad: Sentry + Backup + Uptime (7 jun 2026, sesión 2)

> Continuación de `handoff-cowork-2026-06-07.md`. Léelo al retomar en una sesión nueva de Cowork.
> Rol: **Cowork = senior que maneja Claude Code vía prompts**; Andrés es el intermediario que pega
> los prompts en Claude Code y ejecuta en su PC / Railway. Cowork no ejecuta git en el repo: redacta
> prompts concisos, revisa los diffs/resultados que devuelve Claude Code, y opera servicios externos
> por navegador cuando hace falta (UptimeRobot).

## Objetivo de la sesión

Cerrar la **red de seguridad** que quedó pendiente en el handoff anterior: Sentry, backups
automáticos + off-site, y monitor de uptime. Todo completado salvo activar el DSN de Sentry.

## Lo que se hizo (todo mergeado en `main`)

- **Sentry cableado en api/bot/worker** (antes solo `sentry_dsn` declarado, nunca inicializado).
  - Nuevo `core/observability.py` → `init_sentry(service, settings=None) -> bool`. **No-op real si
    el DSN está vacío** (dev y los 605 tests intactos). Setea tag `service`=api|bot|worker.
  - Cableado: `apps/api/main.py` (1ª línea del `lifespan`), `apps/bot/main.py` (en `crear_app`),
    `apps/worker/main.py` (1ª línea de `on_startup`).
  - Settings nuevos: `sentry_environment` (`SENTRY_ENVIRONMENT`, def. "production"),
    `sentry_traces_sample_rate` (`SENTRY_TRACES_SAMPLE_RATE`, def. 0.0 = solo errores).
  - Dep: `sentry-sdk[fastapi]>=2.0` (auto-activa integraciones FastAPI y ARQ).
  - **Pendiente para activarlo:** poner `SENTRY_DSN` en los 3 servicios de Railway. Hoy va vacío.

- **Backup semanal OPCIONAL + off-site** (commits `8f99ff6`, `4078cf4`, + fix UTF-8).
  - Gate `backup_enabled` (`BACKUP_ENABLED`, **on** ahora en `.env.prod`; `--force` lo ignora). El
    gate va DESPUÉS de `cargar_env_prod()`; `--verify` no pasa por el gate.
  - Retención: `podar_backups()` puro + `--podar SEMANAS` (default 8).
  - **Off-site** `copiar_offsite()` + `_copiar_offsite_seguro()`: copia el árbol (control + tenant) a
    `BACKUP_OFFSITE_DIR`. Si la carpeta no está montada → AVISO y el backup local NO falla.
    Hoy: `BACKUP_OFFSITE_DIR=C:\Users\Dell\OneDrive\ferrebot-backups` (OneDrive de Andrés).
  - Wrapper `tools/backup_daily.ps1`: verifica Docker, fija `PG_DUMP`/`PG_RESTORE` a `postgres:18`,
    `PYTHONUTF8=1`, corre `python -m tools.backup_db --podar 8`, loguea a `backups\logs\`, propaga
    exit code. `backups/` está en `.gitignore` (los .dump llevan datos de prod).
  - **Automatizado:** tarea de Windows `"FerreBot Backup Prod"`, SEMANAL domingos 20:00, corre el
    wrapper. **Probado con `schtasks /Run` → código 0**, backup local + off-site a OneDrive ✓.

- **Uptime (UptimeRobot, plan free)** — 2 monitores HTTP, cada 5 min, alertas por e-mail:
  - API → `https://ferrebot-saas-production.up.railway.app/health`
  - Bot → `https://ferrerojobot-production.up.railway.app/health`
  - Cuenta: `usoclaude1@gmail.com` (login con Google). Alertas a ese correo (NO a andresfmalo05).
  - **NO** se creó status page pública (se saltó a propósito).

- **Fix `/health` acepta HEAD** (commit `5920923`). UptimeRobot free **fuerza HEAD** (el selector de
  método es de pago) y `@app.get("/health")` devolvía **405**. Cambiado a
  `@app.api_route("/health", methods=["GET","HEAD"])` en `apps/api/main.py` y `apps/bot/webhook.py`.
  `/ready` sigue solo GET. Verificado en prod: HEAD → 200 en ambos. Monitores en verde.

## Estado de producción

- Railway: Postgres (PG 18.4), Redis, `api`, `bot`, `worker` vivos.
- Uptime: api y bot en verde (HEAD 200).
- Backup: tarea semanal activa en el PC de Andrés; última corrida manual OK (control + tenant +
  off-site a OneDrive). Restore probado en sesiones anteriores.
- Sentry: código listo, **inactivo** hasta setear `SENTRY_DSN` en Railway.

## Gotchas operativos CRÍTICOS (de esta sesión)

1. **Backup automático = consola no interactiva → cp1252.** Los `print()` con `→`/`✓` reventaban con
   `UnicodeEncodeError` bajo Task Scheduler (en consola interactiva no se ve). Resuelto: `main()` de
   `backup_db.py` hace `sys.stdout/stderr.reconfigure(encoding="utf-8", ...)` + `PYTHONUTF8=1` en el
   wrapper. Los símbolos salen "mojibake" en el `.log` (cosmético), el backup queda íntegro.
2. **La tarea semanal necesita el PC encendido + Docker Desktop abierto** los domingos 20:00. Si no,
   esa semana no corre → ponerse al día con `schtasks /Run /TN "FerreBot Backup Prod"`.
3. **UptimeRobot free solo pinguea con HEAD** (método de pago). Cualquier endpoint a monitorear debe
   responder a HEAD, no solo GET.
4. Siguen vigentes los gotchas del handoff anterior: `.env.prod` con URLs **públicas** de Railway,
   `pg_dump` ≥ servidor vía Docker `postgres:18`, NO cambiar `SECRETS_MASTER_KEY`, cerrar la terminal
   tras ops contra prod.

## Pendiente (con prioridad)

**Red de seguridad — remates menores (opcionales):**
- Activar Sentry: crear proyecto nuevo en Sentry para `ferrebot-saas` (no reusar el del proyecto
  viejo, para no mezclar issues) y poner `SENTRY_DSN` en api/bot/worker en Railway.
- Mover alertas de UptimeRobot a `andresfmalo05@gmail.com` (hoy van a `usoclaude1@gmail.com`); requiere
  añadir y verificar ese correo como contacto.
- Correr el prompt de doc del runbook (sección de monitoreo/uptime en `docs/runbook.md`).
- Monitor del **worker**: no tiene HTTP; su salud hoy depende de Sentry + reinicio de Railway.

**Frentes grandes del proyecto (no-bot):**
- Migración real de Punto Rojo (Fase 15): clientes/aliases, **conteo físico de inventario** +
  indicador de stock negativo (631 en 0), continuidad de consecutivos DIAN, validación de paridad,
  corte del viejo.
- Dashboard: **Ventas Rápidas con fracciones/unidades** (hoy solo vende unidades normales); panel
  super-admin de onboarding; **flujo de caja** (cuadre → reporte → proyección).
- Facturación: asíncrono DIAN (necesita **sandbox MATIAS**); resto fiscal.
- SaaS comercial (Fase 16): billing, 2ª empresa.

**Bot — sesión intensiva dedicada (decisión de Andrés):**
- **BUG de precio:** lija por cm → "20 cm" cobró **$400.000** (debe ser **$4.000**). Falta modelar
  "precio por N unidades" en el motor/datos (el viejo hacía ÷100).
- Slot-filling, desambiguación con BOTONES, reglas de precio en datos/motor (no en prompt), aliases
  que crecen, evals + observabilidad, pulir botones.

## Cómo retomar (prompt para nueva sesión de Cowork)

> Pega esto al abrir la nueva sesión:

```
Retomamos FerreBot SaaS. Lee docs/handoff-cowork-2026-06-07-red-seguridad.md +
docs/handoff-cowork-2026-06-07.md + CLAUDE.md + .claude/rules/. Tu rol: senior que
maneja Claude Code vía prompts concisos; yo (Andrés) los pego en Claude Code y ejecuto.
La red de seguridad ya quedó cerrada (Sentry cableado pero sin DSN, backup semanal +
off-site automático, uptime en verde). Quiero arrancar un frente grande visible:
propón un plan para [Ventas Rápidas con fracciones | flujo de caja | migración de PR],
o el que recomiendes, y empecemos.
```

Sugerencia de Cowork: el frente más autocontenido y visible es **Ventas Rápidas con fracciones** o
**flujo de caja** en el dashboard. El **bug del bot** es importante pero conviene la sesión intensiva
dedicada. Activar el **DSN de Sentry** es un remate de 5 minutos cuando quieras telemetría real.
