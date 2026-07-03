# Handoff Cowork — Plataforma de agentes de WhatsApp (8 jun 2026)

> Léelo al retomar en una sesión nueva de Cowork. **Rol:** Cowork = senior que diseña y redacta
> prompts concisos para Claude Code; **Andrés** los pega en Claude Code, ejecuta, y opera servicios
> externos (Railway, Kapso, Google Cloud) por navegador. Cowork no ejecuta git en el repo: redacta
> prompts, revisa diffs/resultados, y usa Claude-in-Chrome cuando hace falta operar una web.

## Qué se hizo esta sesión (enorme)

Partimos de "¿qué construir para vender SaaS a negocios de Cartagena?" y terminamos con una
**plataforma multi-tenant de agentes de WhatsApp de atención al cliente, viva en producción**, con un
tenant piloto (clínica demo) que **agenda citas, responde dudas, reconfirma y escala a humano por
WhatsApp**, y espeja las citas en Google Calendar.

**Estrategia/decisiones (docs):**
- `docs/saas-mercado-cartagena.md` — qué construir para Cartagena (POS electrónico, Bre-B, WhatsApp).
- `docs/whatsapp-agentes-arquitectura.md` — arquitectura: **runtime genérico + capability packs + datos
  por tenant**; BSP = **Kapso** (no Tech Provider directo); aislamiento por DB-per-tenant.
- `docs/pack-agenda-citas.md` — diseño del pack Agenda.
- `docs/adr/0006-agenda-google-calendar-sync.md` — service account vs OAuth, write-only.
- `docs/DEPLOY-RAILWAY-PILOT.md` — runbook de deploy. `docs/DEV-LOCAL.md` — correr en local.

**Construido (todo en `main`, desplegado en Railway):**
- **Canal WhatsApp/Kapso** (`apps/wa/`): webhook `POST /wa/webhook` (firma HMAC fail-closed, dedup por
  message id), resolución de tenant por `phone_number_id` (tabla control `wa_numeros`), bucle del
  agente (`apps/wa/agent.py`, memoria de conversación `MemoriaWa` en Redis), envío Kapso
  (`apps/wa/kapso.py`: `enviar_texto` + `enviar_plantilla`). LLM = **Claude** (`LLM_PROVIDER=claude`).
- **Pack Agenda** (`modules/agenda/`): datos (servicios/recursos/disponibilidad/bloqueos/agenda_config/
  citas), **motor** (`slots.py` + `service.py`, advisory lock anti-doble-reserva), **herramientas**
  (`ai/agenda_tools.py`), **router** + **dashboard** (TabAgenda con calendario de alta fidelidad,
  "Acción Requerida"). Reglas configurables en `agenda_config`.
- **Escalar a humano / handoff** (`modules/conversaciones/`, `ai/handoff_tools.py`): herramienta
  transversal `escalar_humano`, **pausa del agente** mientras está en `humano`, tab **Conversaciones**
  (bandeja). El humano responde **desde el Inbox de Kapso** (mismo número del negocio).
- **Google Calendar sync** (opcional por tenant, `modules/agenda/gcal.py`): service account,
  `agenda_config.google_calendar_id`, `citas.gcal_event_id`, write-only, **color por estado/confirmación**.
- **Reconfirmación anti-no-show**: cron ARQ (`reconfirmaciones_agenda`, cada 15 min), recordatorio por
  **plantilla** (`recordatorio_cita`), estados `esperando|reconfirmada|en_riesgo`, `corte_riesgo_horas`;
  el agente interpreta "sí/no" (`reconfirmar_cita`). **No libera el cupo si no responde** (queda en riesgo).
- **Pack FAQ / conocimiento** (`modules/faq/`, `ai/faq_tools.py`): `responder_faq` (recuperación simple
  **detrás de un puerto** → cambiar a RAG/embeddings luego sin tocar el agente), tab **Conocimiento**,
  flag `pack_faq`. El agente no inventa: si no hay info → escala/no responde.

## Estado de producción (Railway)

- **Proyecto Railway:** `agile-embrace` / entorno `production`. Servicios: **Postgres**, **Redis**,
  **Ferrebot-Saas** (=API, `SERVICE_TYPE=api`, dominio `https://ferrebot-saas-production.up.railway.app`),
  **Worker** (`SERVICE_TYPE=worker`), **FerreRojo_bot** (bot Telegram de PR — **no se usa** en el piloto WhatsApp).
- **Config-as-Code:** API → `railway.api.toml` (pre-deploy migraciones); Worker/bot → `railway.toml`.
- **Tenant piloto:** `clinica-demo` (empresa_id=2). Flags ON: `pack_agenda`, `canal_whatsapp`, `pack_faq`.
  Sembrado: 2 recursos (Dra. García, Lic. Martínez), 3 servicios (Limpieza 40m/$80k, Blanqueamiento
  60m/$200k, Consulta 30m/$50k), disponibilidad L-V 08-12 y 14-18, `modo_confirmacion=manual`, 4 entradas FAQ.
- **WhatsApp:** número de Kapso de Palmarito (**+57 320 6213221**, `phone_number_id=1176767388843502`)
  **mapeado a clinica-demo** (`wa_numeros`). Webhook de Kapso → `…up.railway.app/wa/webhook`.
- **Probado EN VIVO por WhatsApp:** agendar cita ✓, responder FAQ ✓, escalar a humano ✓, evento en
  Google Calendar ✓. (Reconfirmación: lista, falta aprobación de la plantilla — ver gotchas.)
- **Migraciones aplicadas:** control `0003_wa_numeros`; tenant `0008_agenda` `0009_conversaciones`
  `0010_gcal_sync` `0011_reconfirmacion` `0012_faq`.

## Gotchas operativos CRÍTICOS

1. ~~El pre-deploy NO aplica las migraciones de TENANT.~~ **RESUELTO.** El `preDeployCommand`
   (`railway.api.toml`, Config Path correcto) ya corre `python -m tools.migrate_tenants` en cada
   deploy; **no se corre a mano**. El runner está endurecido: banner al inicio/fin con nº de empresas
   y slugs (`migrate_tenants: 1 empresa(s) → [clinica-demo] OK`), **sale != 0 si encuentra 0 empresas**
   (un join/filtro roto NO pasa como deploy verde; `--allow-empty` solo para el primer deploy sin
   tenants) y exit 1 si alguna falla. En `railway logs` busca `migrate_tenants_inicio`/`_fin`.
2. **Acceso al dashboard de prod:** no hay login para clinica-demo (el login es Telegram widget, no
   configurado). Se entra con **dev token**: `railway ssh` → `python -m tools.dev_token clinica-demo` →
   pegar las 3 líneas `localStorage.setItem(...)` en la **consola del navegador** en
   `https://ferrebot-saas-production.up.railway.app`. (El JWT lleva el tenant en el claim → el resolver
   lo usa; no necesita subdominio.)
3. **Flags sin UI:** se prenden en el control DB con `python -m tools.set_feature <slug> <feature> on`
   (vía `railway ssh`). `seed_clinica_demo` solo enciende `pack_agenda` + `canal_whatsapp`.
4. **Seed/provisioning en prod = EN-RED** (por `railway ssh`, no desde local) para que la URL del tenant
   guarde el host privado.
5. **Plantilla de recordatorio:** `recordatorio_cita` (UTILITY, es, **sin variables**) creada en Kapso,
   estado **Submitted (pendiente de aprobación de Meta)**. Para que el recordatorio se envíe faltan:
   (a) que Meta la apruebe, (b) var **`KAPSO_TEMPLATE_RECORDATORIO=recordatorio_cita`** (+ `…_IDIOMA=es`)
   en el **Worker**. Sin eso el cron corre pero no envía (resiliente).
6. **NO meter el número de Kapso en la app de WhatsApp Business** (rompe la Cloud API salvo Coexistencia).
   El humano atiende desde el **Inbox de Kapso** (`app.kapso.ai → Inbox`), mismo número del negocio.
7. **Variables clave en Railway (API + Worker según aplique):** `LLM_PROVIDER=claude`,
   `LLM_MODEL_WORKER=claude-haiku-4-5-20251001`, `LLM_MODEL_ORQUESTADOR=claude-sonnet-4-6`,
   `ANTHROPIC_API_KEY`, `KAPSO_API_KEY`, `KAPSO_API_BASE=https://api.kapso.ai/meta/whatsapp/v24.0`,
   `KAPSO_WEBHOOK_SECRET`, `GOOGLE_SERVICE_ACCOUNT_JSON`. **No cambiar `SECRETS_MASTER_KEY`.**
8. Gotcha de código ya resuelto: `MissingGreenlet` al serializar `actualizado_en` en upserts → usar
   `datetime.now(COLOMBIA_TZ)` + `await refresh()` tras `flush()` (patrón aplicado en agenda_config).

## Pendiente (con prioridad)

**En vuelo — verificar el último fix (recién aplicado, sin confirmar en prod):**
- (a) Tras "Resolver / Devolver al bot", el agente **re-escalaba** con el siguiente "hola" → fix: limpiar
  `MemoriaWa` al resolver + ajustar prompt (escalar solo ante petición explícita y actual). **Verificar.**
- (b) El tab **Conversaciones** **no se actualiza en vivo** (ni SSE ni botón refrescar; solo al cambiar de
  tab) → fix de tiempo real. **Verificar** que la escalada aparezca sola y el botón refresque.

**Remates del piloto:**
- Aprobar la plantilla `recordatorio_cita` + poner `KAPSO_TEMPLATE_RECORDATORIO` → probar el ciclo
  anti-no-show completo (recordatorio → sí/no → reconfirmada/en_riesgo + color en Calendar/dashboard).
- ~~Arreglar el pre-deploy para que `migrate_tenants` corra solo~~ → **hecho** (ver gotcha #1).
- Activar **Sentry DSN** (telemetría; ya cableado).
- **Pulir el agente:** slot-filling/captura de nombres (salió "Totty Andres"), desambiguación de servicio/fecha.

**Camino al piloto REAL (cuando haya negocio):**
- Conseguir el negocio amigo (no técnico, lo de Andrés).
- Onboarding de su data real + conectar SU número de WhatsApp (vía Kapso).
- Acceso al dashboard para el negocio: login email/contraseña + dominio propio + subdominio por empresa
  (hoy: dev token; el resolver ya soporta subdominio/`base_domain`).

**Producto SaaS (para vender a varios):**
- Onboarding autoservicio / panel super-admin (alta de tenant sin scripts).
- Billing/planes. PgBouncer (al crecer los tenants).

**Features que suman:**
- RAG real del FAQ (embeddings/pgvector) — el puerto ya lo deja aislado.
- Recordatorio **personalizado** (plantilla con variables: nombre/servicio/hora) — hoy es texto fijo.
- Chat del humano **dentro del dashboard** (hoy se usa el Inbox de Kapso).
- **Memoria de largo plazo por cliente** (perfil/resumen en la BD) — distinto del buffer de Redis.
- Toggle claro "Dashboard y/o Google Calendar, el negocio elige" (el sync ya es opcional).

## Mapa rápido de archivos nuevos

```
apps/wa/{webhook,kapso,agent,ports,wiring}.py     — canal WhatsApp + runtime del agente
apps/worker/main.py                                — crons: atender_mensaje_wa, reconfirmaciones_agenda
ai/{agenda_tools,handoff_tools,faq_tools}.py       — herramientas del agente (por pack + transversales)
modules/agenda/{models,schemas,repository,service,slots,gcal,router,errors}.py
modules/conversaciones/{...}                        — handoff (estado bot|humano)
modules/faq/{models,schemas,repository,service,retrieval,router,errors}.py
core/tenancy/{models,control_repo,resolver}.py      — wa_numeros, resolución de tenant
tools/{seed_clinica_demo,seed_wa_numero,dev_token,set_feature}.py
migrations/control/0003_wa_numeros ; migrations/tenant/0008..0012
railway.toml + railway.api.toml                     — config-as-code (pre-deploy migraciones en API)
dashboard/src/tabs/{TabAgenda,TabConversaciones,...}.jsx
```

## Cómo retomar (prompt para nueva sesión de Cowork)

```
Retomamos FerreBot SaaS — plataforma de agentes de WhatsApp. Lee
docs/handoff-cowork-2026-06-08-whatsapp-agentes.md + docs/whatsapp-agentes-arquitectura.md +
docs/pack-agenda-citas.md + CLAUDE.md + .claude/rules/. Tu rol: senior que me redacta prompts
concisos para Claude Code; yo (Andrés) los pego, ejecuto, y opero Railway/Kapso/Google por navegador
(puedes usar Claude-in-Chrome si hace falta). YA está en producción en Railway (proyecto agile-embrace)
un agente de WhatsApp multi-tenant que agenda, responde FAQ, escala a humano y espeja a Google Calendar,
con el tenant piloto clinica-demo. OJO con los gotchas del handoff (dev_token para el dashboard de
prod, set_feature para flags, plantilla de recordatorio pendiente
de aprobación). Quiero seguir con: [verificar los 2 fixes en vuelo (re-escalación + tiempo real de
Conversaciones) | arreglar el pre-deploy | pulir el agente | conseguir/onboarding del piloto real | lo
que recomiendes]. Empecemos.
```
