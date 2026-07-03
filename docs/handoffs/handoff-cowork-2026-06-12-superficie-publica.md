# Handoff Cowork — Superficie pública Melquiadez COMPLETA en main (12 jun 2026)

> Léelo al iniciar la sesión nueva. **Rol Cowork:** senior que escribe planes/prompts para Claude
> Code, REVISA cada diff **leyendo el código real** (GitHub raw vía Claude-in-Chrome o el mount),
> y **no ejecuta git** — lo hace Andrés en Windows. Toda sesión paralela de Claude Code va en
> **worktree propio** y antes de cortar rama se sincroniza `main` local.
> Contexto previo: `docs/handoff-cowork-2026-06-10-cierre.md` (sigue vigente lo de POS/DIAN).

## Qué se hizo esta sesión (plan → 8 fases → todo mergeado)

Se planeó y ejecutó **completa** la superficie pública de Melquiadez. El plan vive en
`docs/plan-melquiadez-superficie-publica.md` (+ fila en CLAUDE.md). Decisiones: dominio
**melquiadez.com** (comprado); sign-in EN la landing; 4 verticales demo; dirección visual
"realismo mágico moderno".

**Mergeado a `main` (todos con CI verde, en este orden):**

| PR | Entrega |
|---|---|
| #38 | 3 tenants demo (barbería El Patio, restaurante Brasa, hotel Brisa) por manifiesto + identidades demo (rol vendedor, `IdentidadExtra` sin password, `extra="forbid"`) + datos vivos relativos a hoy (`tools/seed_demo_transaccional.py`) + cron nocturno `resembrar_demos` (04:10 CO) + **guardarraíl doble** (lista `DEMO_TENANT_SLUGS` ∧ sufijo `-demo`) |
| #39 | Branding (sello M en `landing/marca/`) + landing nueva Vite+React+shadcn (`landing/`): hero palabra rotante, acordeón de verticales, /login split-card, /demo. Deploy Cloudflare Workers assets |
| #41 | `LABELS_RESERVADOS = {app, api, www, admin}` en `core/tenancy/resolver.py` + slug prohibido en manifiesto |
| #42 | `tools/switch_demo.py` — switch del número Kapso entre demos en un comando (re-apunta `wa_numeros` + limpia `MemoriaWa` del tenant entrante Y saliente; alias `barberia`→`barberia-demo`) |
| #40 | Presets de branding por vertical (`core/tenancy/branding_presets.py`: aurora/brasa/navaja/brisa/lienzo/melquiadez) → `GET /config` resuelve tokens planos → theming runtime. `preset` nuevo, `tema` queda de fallback compat. Default plataforma = `melquiadez`; el `#C8200E` pasó a branding EXPLÍCITO de Punto Rojo |
| #43 | Puente landing→dashboard: `apps/api/cors.py` (`ScopedCORSMiddleware`, SOLO `/api/v1/auth/login/password` y `/auth/reset/solicitar`, origins por `CORS_ALLOW_ORIGINS`, sin credenciales) + handoff por fragmento `#token=` (el dashboard lo guarda y limpia con replaceState) + redirect `next=` sin open-redirect |
| #44 | Cierre: `tests/test_e2e_superficie_publica.py` + onboarding-tenant.md actualizado + barrido de-branding |

`main` local == origin/main (fast-forward limpio). Worktrees/ramas mergeadas: Andrés pidió limpiarlos
(puede estar hecho ya).

## Estado: el código está TERMINADO. Falta solo OPERACIÓN (Andrés, por navegador/ssh)

1. **Cloudflare**: apex `melquiadez.com` → landing (Workers assets); `app` + `*.melquiadez.com` → CNAME a Railway.
2. **Railway**: custom domains `app.` y `*.melquiadez.com` en el servicio API; vars
   `BASE_DOMAIN=melquiadez.com` y `CORS_ALLOW_ORIGINS=https://melquiadez.com`; **redeploy**.
3. **Provisionar demos en prod** (`railway ssh` al Worker, SIEMPRE post-redeploy — contenedor fresco):
   `provision_from_manifest` × 3 (barberia/restaurante/hotel-demo.manifest.example.yaml) +
   `python -m tools.seed_demo_transaccional`. Capturar tokens de set-password (admin + demo de cada
   tenant) y fijar las contraseñas demo (las usa el botón "Ver demo" de la landing).
4. **Probar flujo de venta**: melquiadez.com/login → identidad demo → barberia-demo.melquiadez.com;
   `python -m tools.switch_demo barberia` + WhatsApp al +57 320 6213221.
5. **Kapso**: avatar/nombre Melquiadez al número demo (sello M en `landing/marca/`).
6. Al día siguiente: `railway logs` del Worker → `resembrados=4` (~04:10 CO).

## Gotchas nuevos de esta sesión

- **Revisar diffs de ramas pusheadas**: GitHub raw vía Claude-in-Chrome con
  `github.com/<repo>/raw/<rama>/<archivo>` (redirige con token). `raw.githubusercontent.com` directo
  da 404 (repo privado) y las páginas blob virtualizan el código (innerText vacío).
- **El endpoint de login por email es `POST /api/v1/auth/login/password`** (no `/auth/login`).
- **No renombrar** las llaves `ferrebot_*` de localStorage del dashboard (solo internas; renombrar
  desloguea/resetea preferencias de todos). El comentario "FerreBot original" en useAuth es inocuo.
- `DEMO_TENANT_SLUGS` tiene default con las 4 demos en código — no hay que setear env en prod salvo
  para cambiar la lista.
- Deuda menor anotada: actions `checkout@v4`/`setup-uv@v5` corren en Node 20 (deprecado, muere
  16-sep-2026) — chore de CI pendiente, no urge.

## Posibles siguientes frentes (decidir con Andrés)

- Operación de los pasos 1-6 (Cowork puede asistir con Claude-in-Chrome en Cloudflare/Railway/Kapso).
- `POST /auth/demo` (v2 del acceso demo: JWT corto, sin credenciales públicas) — plan §4.
- Botón de switch demo en el panel super-admin (ADR 0010) — plan §6 v2.
- Pulir agente/datos de las demos nuevas tras probarlas en vivo.
- Frente B pendiente del handoff anterior: Bre-B/PSPs (ADR 0013) y gate sandbox MATIAS.

## Prompt para retomar (pegar en la sesión nueva de Cowork)

```
Retomamos FerreBot SaaS / Melquiadez. Lee docs/handoff-cowork-2026-06-12-superficie-publica.md +
docs/plan-melquiadez-superficie-publica.md + CLAUDE.md + .claude/rules/. Tu rol: senior que me
redacta prompts para Claude Code y revisa los diffs leyendo código real (GitHub raw por Chrome o el
mount); yo ejecuto git/Railway/Cloudflare/Kapso. La superficie pública (landing React + sign-in +
subdominios demo + 4 tenants demo + presets de branding + switch del número Kapso) está COMPLETA y
mergeada en main (PRs #38-#44, CI verde). Falta solo la operación: DNS/dominios, provisionar las
demos en prod y probar el flujo en vivo (checklist de 6 pasos en el handoff). Quiero seguir con:
[operar los 6 pasos conmigo | pulir lo que salga de probar las demos | POST /auth/demo | lo que
recomiendes]. Empecemos.
```
