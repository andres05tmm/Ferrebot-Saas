# Handoff Cowork — Manifiesto de tenant + provisionador de un paso (8 jun 2026)

> Léelo al retomar en una sesión nueva de Cowork. **Rol:** Cowork = senior que diseña el plan y redacta
> prompts concisos para Claude Code; **Andrés** los pega en Claude Code, ejecuta, revisa diffs y opera
> Railway/Kapso/Google/GitHub por navegador. Cowork no ejecuta git en el repo: planifica, revisa el
> código que Claude Code escribe, y promptea la siguiente fase.

## Qué se hizo esta sesión

Se cerró el frente de **infraestructura de onboarding**: dar de alta un cliente real pasó de "copiar y
editar un seed bespoke en Python + 2 comandos sueltos" a **escribir un manifiesto YAML + correr un
comando idempotente**. Implementa el **ADR 0007** en 4 fases, todo mezclado a `main` con CI en verde.

**Decisión (docs):**
- `docs/adr/0007-manifiesto-tenant-y-provisionador.md` — el ADR: manifiesto YAML (un archivo por tenant),
  registro de packs declarativo, contrato del provisionador, validación de aceptación. Trade-offs incluidos.
- `docs/plan-provisionador-manifiesto.md` — el plan por fases + los prompts que se le pasaron a Claude Code.

**Construido (en `main`, PR #1 mezclado, merge commit `3fcd281`):**
- **Manifiesto** (`tools/manifest/`): `schema.py` (Pydantic v2, `extra="forbid"`), `loader.py`
  (`yaml.safe_load` → acepta YAML y JSON), `validacion.py` (features contra `core/tenancy/catalogo`,
  dependencias del set efectivo, formato de franjas/días, `presta`→servicio declarado, y **coherencia
  flag↔datos**: declarar datos de un pack sin su flag activa = error).
- **Loaders de packs** (`tools/manifest/packs/`): `agenda.py` y `faq.py` (upsert idempotente, driver
  sync psycopg, `MONEY`→`Decimal`), `registry.py` (`Pack(flag, loader, tablas)` + `packs_activos`).
- **Provisionador** (`tools/provision_from_manifest.py`): orquesta valida → base
  (`provision_tenant.provision_tenant_full`) → loaders de packs activos → `wa_numeros`
  (reusa `seed_wa_numero.seed`) → verifica + resumen de una línea. **Idempotente end-to-end**
  (sin transacción global a propósito: hay `CREATE DATABASE` y 3 planos de BD).
- **Manifiesto canónico de ejemplo:** `tools/onboarding/clinica-demo.manifest.example.yaml` (alineado
  literalmente al seed bespoke; la prueba de aceptación verifica equivalencia y atrapó divergencias reales
  —buffers, categoría— que se corrigieron).
- **CI** (`.github/workflows/ci.yml`): GitHub Actions con Postgres 18 + Redis 7 (health checks),
  `uv run pytest`. Corre en cada PR y push a `main`.
- **fix(wa):** fuga de conexión asyncpg en `apps/wa/agent.py` (generador async no cerrado ante excepción
  → colgaba el teardown en Linux). Resuelto con `contextlib.aclosing`. **Lo destapó el CI** (pasaba en
  Windows local, fallaba en Linux). Era un bug de producción real (fuga por cada turno fallido del agente).
- **Limpieza:** `seed_clinica_demo.py` deprecado (se conserva como referencia de la prueba de aceptación);
  `.gitignore` cierra hueco vs. regla #5 (ignora `tools/onboarding/*.yaml` reales, `*.example.yaml` se versiona);
  aserciones de paridad de esquema al día (tabla `conocimiento`/FAQ 0012, enum `cita_confirmacion`/0011).

## Estado del repo

- `main` = `origin/main` = `3fcd281`. CI verde (`test pass`, ~3 min). Rama de la feature borrada.
- **Tests:** suite completa verde con Postgres + Redis arriba (local y CI). Cuatro suites nuevas:
  `tests/test_manifest.py`, `test_manifest_packs.py`, `test_provision_from_manifest.py`, `test_manifest_aceptacion.py`.

## Cómo se onboardea un cliente AHORA

1. Copiar `tools/onboarding/clinica-demo.manifest.example.yaml` → `tools/onboarding/<slug>.yaml` (gitignored).
2. Rellenar: identidad (slug/nombre/**nit obligatorio**), `plan.features` (packs), datos de pack
   (agenda: servicios/recursos/disponibilidad/persona; faq: entradas), `canal.whatsapp.phone_number_id`.
3. Correr `python -m tools.provision_from_manifest --from tools/onboarding/<slug>.yaml`. Re-correr es seguro.
4. **En prod: EN-RED vía `railway ssh`** (no desde local), para que la URL del tenant guarde el host privado.
5. Externo a Kapso/Google (manual por navegador, irreducible): crear/conectar el número, webhook,
   aprobar plantillas, compartir el calendario con el service account.

## Gotchas

- `nit` es **obligatorio** (la columna `empresas.nit` del control DB es NOT NULL + UNIQUE).
- Validación de **coherencia**: datos de un pack sin su flag activa = error (atrapa el "olvidé prender el
  pack"); la inversa (flag sin datos) es válida — el negocio nutre su data después.
- El provisionador **no es atómico global** (imposible: `CREATE DATABASE` + control DB + BD del tenant).
  La garantía es **idempotencia**: re-correr tras un fallo parcial converge al mismo estado.
- Manifiestos con valores reales: **gitignored**; los secretos siguen **cifrados** en el control DB.
- `seed_wa_numero` y `set_feature` se mantienen como helpers (el primero lo reusa el provisionador; el
  segundo es un toggle operacional). `seed_clinica_demo` quedó deprecado.

## Pendiente (con prioridad)

**Siguiente frente — Fase 5 (lo que el panel necesita, se cuelga de este provisionador):**
- **Login real (email/contraseña).** Hoy NO existe infraestructura de password: el login es Telegram
  Login Widget (`modules/auth/`), cero hashing. Es **greenfield**: columna+hash en `usuarios`, endpoint,
  formulario, invitación/reset. El resolver ya soporta subdominio (media tarea de *ruteo* hecha). Bloquea
  entregar el dashboard a un cliente que edite su propia data (hoy se entra con `dev_token`).
- **Panel super-admin self-serve** = un formulario que **produce este manifiesto y llama a
  `provision_from_manifest`**. El backend ya está; el panel es la piel encima. Construir el panel ANTES
  del provisionador habría sido al revés — por eso este frente va ahora.

**Remates del piloto WhatsApp (del handoff anterior, siguen vigentes):**
- Aprobar la plantilla `recordatorio_cita` + var `KAPSO_TEMPLATE_RECORDATORIO` (cierra el anti-no-show).
- Pulir el agente (slot-filling/captura de nombres).
- **Conseguir el negocio amigo** y onboardearlo con su data real (ya con el manifiesto, es trivial).

**Producto SaaS:** billing/planes; PgBouncer al crecer los tenants; RAG real del FAQ (el puerto ya lo aísla).

## Cómo retomar (prompt para nueva sesión de Cowork)

```
Retomamos FerreBot SaaS — plataforma de agentes de WhatsApp. Lee
docs/handoff-cowork-2026-06-08-manifiesto-provisionador.md +
docs/handoff-cowork-2026-06-08-whatsapp-agentes.md + docs/adr/0007-manifiesto-tenant-y-provisionador.md +
CLAUDE.md + .claude/rules/. Tu rol: senior que diseña el plan y me redacta prompts concisos para Claude
Code; yo (Andrés) los pego, ejecuto y reviso. YA está hecho y en main (CI verde) el onboarding declarativo
(manifiesto YAML + provision_from_manifest). Quiero seguir con: [Fase 5: login real + panel super-admin |
remates del piloto WhatsApp | conseguir/onboardear el negocio real | lo que recomiendes]. Empecemos.
```
