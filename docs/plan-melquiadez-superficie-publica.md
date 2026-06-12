# Plan — Melquiadez: superficie pública (landing, sign-in, demos y switch de número)

> Estado: **Implementado** (olas M1–M8 mezcladas en `main`; cierre 12 jun 2026). El **código** de todas
> las olas está hecho; lo único pendiente es **infra/ops externo** (DNS wildcard en Cloudflare, custom
> domains en Railway, rebrand del avatar del número en Kapso) — marcado en el checklist final.
>
> Cubre: rebrand a **Melquiadez**, landing nueva con sign-in
> integrado, subdominios para cuentas demo, demos por vertical (clínica, restaurante, barbería, hotel),
> switch rápido del número Kapso entre demos, y deploy fácil de dashboards para clientes nuevos.
> Construye sobre lo YA hecho: login real (ADR 0009, implementado), packs multi-vertical (ADR 0008),
> manifiesto+provisionador (ADR 0007/0011), resolver con subdominio (`core/tenancy/resolver.py`).

## Decisiones tomadas con Andrés (12 jun)

| Decisión | Elección |
|---|---|
| Dominio propio | **melquiadez.com** (ya comprado) |
| Dónde vive el sign-in | **En la landing** (una sola experiencia de marca; redirige al dashboard) |
| Verticales demo de esta tanda | **Las 4**: clínica (pulir), restaurante, barbería, hotel |
| Dirección visual | **Realismo mágico moderno** (la referencia a Melquíades, sin disfraz de SaaS gringo) |

## 0. Qué existe ya (no construir dos veces)

- **Login email/contraseña multi-tenant**: `modules/auth/login_email.py` + `password_reset.py`,
  directorio `identidades` en control DB, `Login.jsx`/`SetPassword.jsx`/`RecuperarPassword.jsx`,
  grandfather. El JWT lleva el claim `tenant` → el resolver hace el resto. **Lo que falta es la
  EXPERIENCIA**, no el mecanismo.
- **Resolver por subdominio**: `slug.BASE_DOMAIN` ya es la primera señal del resolver. Activar
  subdominios = configurar `BASE_DOMAIN` + DNS wildcard + dominio custom en Railway. Cero código nuevo
  (salvo verificación del slug reservado, ver §3).
- **Provisionador por manifiesto** (ADR 0007/0011): alta de tenant = un YAML + un comando. Es la base
  del "deploy fácil para clientes nuevos" (§7) y de los tenants demo (§5).
- **Mapeo Kapso → tenant**: tabla control `wa_numeros`, upsert por `phone_number_id`
  (`tools/seed_wa_numero.py`). El switch de demo (§6) es una envoltura cómoda sobre esto.
- **5 propuestas de diseño por vertical** en `design-propuestas/` (aurora-clínica, brasa-restaurante,
  brisa-hotel, navaja-barbería, lienzo-genérico): son la base visual de los dashboards demo (§5).
- **Landing** (M3, ✅ hecho): migrada de HTML a mano a **Vite + React + Tailwind** en `landing/` (`/`,
  `/login`, `/demo`), ya con marca Melquiadez; el build estático se sigue desplegando como assets de
  Cloudflare (`wrangler.jsonc → landing/dist`, app `melquiadez-landing`). [La descripción de abajo en §2
  es de cómo se construyó.]

## 1. Branding Melquiadez

**Concepto**: Melquíades traía a Macondo los inventos del mundo ("las cosas tienen vida propia, todo
es cuestión de despertarles el ánima"). Melquiadez le trae al negocio de barrio un empleado que no
duerme. Realismo mágico moderno: misterio + oficio, Caribe + tecnología. Nada de "Revoluciona tu
negocio con IA".

- **Logo**: monograma **M como sello/firma alquímica** en SVG (trazo de pluma con una chispa/cometa),
  + wordmark en serif display. 3 variantes: sello solo (favicon/avatar de WhatsApp), wordmark,
  lockup completo. Se diseña a mano en SVG (iterando en el visual-harness), no clipart de IA.
- **Tipografía**: **Fraunces** (display, los titulares con carácter de imprenta antigua) +
  **Bricolage Grotesque** (ya en uso, se queda para UI/cuerpo).
- **Paleta**: evoluciona la actual (que ya es papel cálido/tinta — buen punto de partida):
  papel `oklch(97.6% .006 85)`, tinta noche `oklch(16% .014 55)`, **oro viejo** como acento de marca
  `oklch(70% .12 80)`, y el acento por vertical se mantiene (ya existe en la landing y en las
  propuestas de dashboard). Dark mode = el modo "noche de Macondo", con más protagonismo.
- **Voz**: poco texto, frases cortas, concretas y en español colombiano. La demo habla por sí sola:
  el centro de la landing es el TELÉFONO mostrando la conversación real del agente, no párrafos.
- **De-branding FerreBot** (FerreBot queda como nombre del producto del tenant 1, Punto Rojo):
  - `landing/index.html`: título, meta description, marca del nav, footer.
  - Dashboard: pantalla de login y shell (hoy hereda branding por tenant — el **default** de
    plataforma pasa de `#C8200E` a la paleta Melquiadez; Punto Rojo conserva su rojo vía branding
    por-tenant, que para eso existe).
  - `README.md` / metadatos públicos del repo: presentar la plataforma como Melquiadez
    (el repo puede seguir llamándose ferrebot-saas; renombrarlo es opcional y aparte).
  - Avatar/nombre del número de Kapso de demo → logo y nombre Melquiadez.

## 2. Landing nueva (reforma total)

**Decisión técnica: la landing pasa de HTML a mano a Vite + React + Tailwind + shadcn/ui** (el mismo
stack del dashboard), porque los componentes de 21st.dev son React/Tailwind y se instalan con
`npx shadcn@latest add <url-del-componente>`. El build estático se sigue desplegando igual en
Cloudflare (`wrangler.jsonc` apunta al `dist/`). SPA con rutas: `/` (landing), `/login`,
`/demo` (selector de demos). Lo bueno del HTML actual (rotación de vertical, tema claro/oscuro,
acentos por vertical, teléfono con chat) **se porta, no se bota**.

**Estructura de secciones (poco texto, mucha demo):**

1. **Hero**: titular corto con la palabra del vertical rotando (`text-rotate` de 21st.dev — es
   exactamente el concepto que ya tiene la landing, pero con la animación bien hecha) + el teléfono
   con la conversación de WhatsApp animándose sola + fondo con shader sutil (aurora/beams en oro
   viejo). Un solo CTA primario: **"Ver una demo"** (+ "Sign in" en el nav, siempre visible).
2. **Selector de verticales**: acordeón de imágenes interactivo (`interactive-image-accordion`) —
   tocar un vertical retematiza el acento y cambia la conversación del teléfono. Cada panel linkea
   a su demo en vivo (subdominio).
3. **Cómo funciona**: 3 pasos visuales (escanea tu catálogo → el agente atiende tu WhatsApp → tú ves
   todo en tu dashboard), con `blur-fade`/scroll reveal. Sin párrafos: un verbo por paso.
4. **El dashboard**: screenshot/video del dashboard real dentro de un marco con
   `container-scroll-animation` (el efecto de zoom-out al hacer scroll).
5. **Prueba social / precios**: diferido hasta tener clientes reales y plan de precios (no inventar
   testimonios). Placeholder: logos de los verticales demo.
6. **Footer mínimo** + CTA final.

**Página `/login` (la joya):** pantalla dividida (`split-login-card` como base): a la izquierda el
formulario limpio (email, contraseña, "olvidé mi contraseña"); a la derecha el panel de marca con
shader animado en oro/tinta y el sello M. Estados de error/bloqueo claros (la API ya devuelve
401 genérico y 429 de lockout). Microcopy con `design:ux-copy`.

### Catálogo extraído de 21st.dev (navegado 12 jun 2026)

Instalación: `npx shadcn@latest add https://21st.dev/r/<autor>/<slug>` (cada página de componente
tiene además "Copy prompt" pensado para agentes). Curado de ~300 componentes vistos:

| Uso | Componente (autor/slug) | Nota |
|---|---|---|
| Sign-in base | `ruixenui/split-login-card` | Split panel marca + form. **Primera opción.** |
| Sign-in alt. | `arunachalam0606/modern-animated-sign-in` | Dark, radial gradients, animado. |
| Sign-in alt. | `hextaui/modern-stunning-sign-in` | Minimal oscuro, muy limpio. |
| Sign-in alt. | `easemize/auth-fuse`, `erikx/sign-in-flow-1`, `jatin-yadav05/sign-in-card-2` | Referencias. |
| Hero: palabra rotante | `danielpetho/text-rotate` (variante `landing-hero`) | Calza 1:1 con el concepto actual. |
| Hero: reveals | `danielpetho/vertical-cut-reveal`, `magicui/blur-fade`, `motion-primitives/text-effect` | Entradas de texto. |
| Hero alternativo | `easemize/cinematic-landing-hero`, `serafim/hero-with-mockup`, `uniquesonu/animated-hero-section` | Si el hero propio se queda corto. |
| Verticales | `thanh/interactive-image-accordion` | Acordeón de imágenes para las 4 demos. |
| Fondo (realismo mágico) | `Scottclayton3d/aurora-flow`, `muhammad-binsalman/ethereal-beams-hero`, `dhiluxui/woven-light-hero` | Shaders de luz; usar UNO, sutil. |
| Fondo geométrico | `kokonutd/background-paths`, `thanh/background-grid-beam`, `magicui/grid-pattern`, `Kain0127/entropy` | Alternativas más sobrias. |
| Chispas/magia | `aceternity/sparkles`, `magicui/meteors`, `aceternity/lamp` | Acentos puntuales (el "ánima"). |
| Scroll showcase | `aceternity/container-scroll-animation`, `YoucefBnm/animated-video-on-scroll` | Para la sección del dashboard. |
| Texto utilitario | `aceternity/flip-words`, `motion-primitives/text-scramble`, `danielpetho/typewriter` | Detalles (ej. el agente "escribiendo…"). |
| Interacción juguetona | `danielpetho/text-cursor-proximity`, `danielpetho/gravity`, `danielpetho/gooey-filter` | Solo si no distraen. |
| Features | `ravikatiyar/feature-carousel`, `shadcnblockscom/shadcnblocks-com-hero115` | Sección "cómo funciona". |

**Regla de uso**: máximo un shader de fondo + una animación de texto por viewport. El realismo
mágico es atmósfera, no feria de fuegos artificiales.

### MCPs/herramientas para diseñar mejor (Claude Code)

- **21st.dev Magic MCP** (`21st.dev/magic`): genera/inserta componentes desde prompt dentro de
  Claude Code. Instalarlo para la fase de landing.
- **shadcn registry**: los `npx shadcn add <url>` de la tabla (no requiere MCP).
- **Playwright MCP (o el visual-harness existente en `dashboard/visual-harness/`)**: screenshots
  automatizados para iterar diseño con feedback visual real (clave: Claude Code "ve" lo que diseñó).
- **Figma MCP** (plugin design ya disponible en Cowork): solo si se diseña el logo/OG-image en Figma.

## 3. Topología de dominios y el flujo de sign-in

```
melquiadez.com                → Cloudflare (landing estática React: /, /login, /demo)
app.melquiadez.com            → Railway API (sirve dashboard dist/ + /api) — entrada de clientes
{slug}.melquiadez.com         → Railway API (mismo servicio; el resolver lee el subdominio)
                            ej: barberia-demo.melquiadez.com, clinica-demo.melquiadez.com
```

- **DNS (Cloudflare)**: apex → Cloudflare assets; `app` y wildcard `*.melquiadez.com` → CNAME al dominio
  de Railway. SSL universal de Cloudflare cubre el primer nivel de wildcard. En Railway: agregar
  `app.melquiadez.com` y `*.melquiadez.com` como custom domains del servicio API.
- **Backend**: setear `BASE_DOMAIN=melquiadez.com` (el resolver ya prioriza `slug.BASE_DOMAIN`).
- ✅ **Cambio de código HECHO**: `_slug_from_host` ya trata `LABELS_RESERVADOS` (`app`, `api`, `www`,
  `admin`) como "sin subdominio" (`core/tenancy/resolver.py`), el manifiesto los prohíbe
  (`tools/manifest/schema.py`, `slug_valido`) y hay tests (`tests/test_resolver_labels_reservados.py`,
  + el smoke E2E `tests/test_e2e_superficie_publica.py` que prueba `app.melquiadez.com` → claim del JWT).
  Sin esto, con wildcard `app.melquiadez.com` resolvería al tenant "app" y nunca caería al claim.

**Flujo de sign-in (landing → dashboard):**

1. Usuario abre `melquiadez.com/login` (página hermosa de la landing).
2. El form hace `POST https://app.melquiadez.com/auth/login` (el endpoint YA existe). Requiere **CORS**
   en la API solo para `/auth/*` y solo desde el origin de la landing (cambio backend pequeño).
3. Con el `{token}` recibido, la landing redirige a `https://app.melquiadez.com/#token=...`
   (**fragmento de URL**: no viaja al servidor, no queda en logs). El dashboard, al arrancar, lee el
   fragmento, guarda el token como hoy (localStorage), lo borra de la URL (`history.replaceState`)
   y carga `GET /config` → shell tematizado del tenant. Cambio frontend pequeño.
4. Error 401/429 → se muestran en la página de login de la landing (mensajes ya definidos por la API).
5. `set-password` y `reset` **se quedan en el dashboard** (ya construidos y testeados); solo se
   re-estilizan con los mismos componentes para que la transición visual sea continua. Los enlaces
   de "olvidé mi contraseña" de la landing apuntan allá.

**Fase 2 (opcional, pulido)**: cookie httpOnly `Domain=.melquiadez.com` emitida por la API en lugar del
fragmento — más limpio y resiste XSS, pero exige tocar el middleware de auth del dashboard. El
fragmento es suficiente para arrancar y no cambia ningún contrato.

**Comodidad extra**: si el usuario entra directo a `barberia-demo.melquiadez.com` sin sesión, el dashboard
lo manda a `melquiadez.com/login?next=barberia-demo`. Tras el login, la redirección respeta `next` (solo
subdominios propios — nunca un redirect abierto).

## 4. Acceso a las demos desde la landing

Objetivo: que un prospecto toque "Ver demo barbería" y caiga en el dashboard demo **sin fricción**,
y que Andrés pueda mostrar el bot por WhatsApp en la misma conversación de venta.

- **v1 (sin código nuevo)**: identidad demo por tenant (`demo@melquiadez.com` / contraseña pública corta),
  rol `vendedor` (no admin: que no puedan romper la demo), creada por el manifiesto de cada demo.
  El botón "Ver demo" de la landing hace el login demo automáticamente (mismo flujo §3) y cae en
  `{vertical}-demo.melquiadez.com`.
- **v2 (pulido)**: endpoint `POST /auth/demo {vertical}` rate-limited que emite un JWT de vida corta
  (30 min) SOLO para tenants demo. Evita credenciales públicas. **Nota de implementación:** la marca de
  "demo" NO se hizo como columna `es_demo`; se resolvió con el setting `demo_tenant_slugs`
  (`core/config/settings.py`, coma-separado, cero migración). Si este endpoint v2 se construye, lee de
  ahí (o se promueve a columna entonces).
- **Higiene de demos**: job nocturno (ARQ, ya hay worker) que resiembra los datos de los tenants
  demo (citas/ventas del día borradas, datos canónicos restaurados). Las demos siempre amanecen
  impecables y con fechas relativas a "hoy".

## 5. Demos por vertical: 4 tenants demo de verdad

Cada demo es un **tenant real provisionado por manifiesto** (ADR 0007) — exactamente el mismo camino
que un cliente pagado, lo cual convierte cada demo en un test de provisioning. Negocios ficticios
con nombre propio (los de `design-propuestas/`):

| Tenant (slug) | Negocio ficticio | Packs | Diseño base | Datos sembrados |
|---|---|---|---|---|
| `clinica-demo` (existe, pulir) | Clínica dental Aurora | `pack_agenda`, `canal_whatsapp`, `pack_faq` | `propuesta-aurora-clinica.html` | Ya tiene; ampliar: más citas/historial creíble |
| `restaurante-demo` | Brasa | `pack_pedidos` (+`pos`, su dependencia en el catálogo), `canal_whatsapp`, `pack_faq` | `propuesta-brasa-restaurante.html` | Menú ~25 ítems, pedidos del día, horas pico |
| `barberia-demo` | El Patio | `pack_agenda`, `canal_whatsapp`, `pack_faq` | `propuesta-navaja-barberia.html` | 3 barberos, servicios, agenda semanal llena |
| `hotel-demo` | Brisa | `pack_reservas` (+`pack_agenda`, su dependencia), `canal_whatsapp`, `pack_faq` | `propuesta-brisa-hotel.html` | Habitaciones, reservas, FAQ check-in |

> ✅ **Hecho:** `tools/manifest/packs/registry.py` ya registra los **4** packs —`pack_agenda`,
> `pack_faq`, `pack_pedidos` (ADR 0016; menú = catálogo del POS, su dependencia) y `pack_reservas`
> (variante noches de agenda)— con sus loaders. Los 4 manifiestos demo provisionan y siembran datos
> vivos (`tools/seed_demo_transaccional.py` + cron `resembrar_demos`).

**Trabajo de dashboard que esto destapa (en orden):**

1. **Cerrar A2/ADR 0008 si falta algo**: que un tenant de servicios NO vea tabs de retail (la
   clínica demo es el canario). Verificar contra `main` actual.
2. **Theming por vertical**: las propuestas de `design-propuestas/` se convierten en **presets de
   branding** (paleta + tipografía + iconografía por vertical) seleccionables en el manifiesto
   (`branding.preset: navaja`). El dashboard ya tematiza por `--color-primary`; extender el branding
   del control DB a un set de tokens (primario, superficie, radio, fuente display) que el shell lee
   de `GET /config`. Así un cliente nuevo de barbería nace con el look "Navaja" sin diseñar nada.
3. **Home "Hoy" por vertical**: el dashboard de demo debe abrir con la vista que vende — agenda del
   día (barbería/clínica), pedidos activos (restaurante), llegadas de hoy (hotel). Gating por packs
   ya existe; es cuestión de qué widget es el primero.
4. **Hotel**: confirmar que `pack_agenda` modela reservas multi-día o si necesita extensión
   (decisión chica tipo ADR si hace falta; no bloquear las otras 3 demos por esto).

## 6. Switch del número Kapso entre demos (minutos → segundos)

Hoy el número (+57 320 6213221, `phone_number_id=1176767388843502`) está mapeado a `clinica-demo`
en `wa_numeros`. Cambiarlo de negocio = re-apuntar esa fila (upsert que ya hace
`tools/seed_wa_numero.py`). Lo que falta es la envoltura de UN comando y la limpieza de estado:

```
python -m tools.switch_demo barberia
```

Hace, en orden: (1) upsert `wa_numeros` → `barberia-demo`; (2) **limpia `MemoriaWa` en Redis** para
ese `phone_number_id` (si no, el agente sigue la conversación anterior con el contexto del negocio
viejo); (3) imprime confirmación: tenant activo, packs encendidos, nombre del negocio que saludará.
Idempotente; `--list` muestra el mapeo actual. El prompt del agente ya es por-tenant (sale de los
packs/datos del tenant), así que no hay nada más que tocar.

**v2 (opcional)**: botón en el panel super-admin (ADR 0010) — "Número demo → [selector de tenant]" —
para hacer el switch desde el teléfono en plena reunión con un prospecto.

⚠️ Nota Kapso: el avatar/nombre del número es uno solo (es UN número). Ponerle marca **Melquiadez**
(no la de un negocio ficticio) para que sirva a cualquier vertical sin chocar.

## 7. Deploy fácil de dashboards para clientes nuevos

El provisionador ya hace lo difícil (base → migrar → sembrar → secretos → admin → identidad con
set-password). Lo que falta para que "dar de alta un cliente" sea un trámite de minutos:

1. **Slug = subdominio automático**: con el wildcard de §3, todo tenant nuevo nace con
   `{slug}.melquiadez.com` funcionando. Cero pasos extra (validar slugs reservados en el manifiesto).
2. **Branding preset por vertical** (§5.2): `branding.preset` en el manifiesto → el cliente nace
   con un dashboard bonito de su gremio, personalizable después.
3. **Checklist de alta** en `docs/onboarding-tenant.md` (actualizar): manifiesto (skill
   onboarding-mágico) → `provision_from_manifest` → enlace set-password al cliente → mapear su
   número Kapso → smoke (`tools/verify_tenant.py`). Meta: **< 30 min** de insumos a dashboard vivo.
4. **Panel super-admin** (ADR 0010) absorbe esto como UI cuando haya volumen; no bloquea.

## 8. Fases y orden de ejecución

| Fase | Entrega | Depende de |
|---|---|---|
| M1 | **Branding**: logo SVG (sello M + wordmark), tokens de marca, de-branding FerreBot en superficies públicas | — |
| M2 | **Infra de dominios**: DNS Cloudflare + custom domains Railway + `BASE_DOMAIN` + slugs reservados + verificación del resolver con `app.` | dominio |
| M3 | **Landing React**: migrar a Vite+React+shadcn, portar lo bueno del HTML actual, secciones §2 con componentes del catálogo | M1 |
| M4 | **Sign-in**: página `/login` en la landing + CORS `/auth/*` + handoff por fragmento + `next=` | M2, M3 |
| M5 | **Tenants demo**: manifiestos de barbería/restaurante/hotel + ampliar clínica + identidades demo + reset nocturno | M2 |
| M6 | **Theming por vertical**: presets de branding desde `design-propuestas/` + branding extendido en `GET /config` + home "Hoy" por vertical | M5 |
| M7 | **Switch Kapso**: `tools/switch_demo.py` + rebrand del número en Kapso | M5 |
| M8 | **Cierre**: checklist de onboarding actualizado, smoke E2E (landing → login → dashboard demo por subdominio), QA visual con el harness | todo |

M1–M2 y M5 pueden ir en paralelo. El camino crítico de "se ve hermoso" es M1→M3→M4; el de
"puedo vender con demos" es M5→M6→M7.

## 9. Riesgos y verificaciones

- **Resolver con labels reservados** ✅ resuelto en código (§3): el fix de `_slug_from_host` está
  mezclado y testeado. Debe estar (lo está) ANTES de prender el wildcard en DNS — sin él
  `app.melquiadez.com` rompe.
- **Pack pedidos/reservas** ✅ resuelto: registry registra los 4 packs con loaders (ver §5).
- **CORS** ✅ resuelto: `apps/api/cors.py` abre CORS QUIRÚRGICO solo para `/auth/login/password` y
  `/auth/reset/solicitar`, y solo desde el origin de la landing (`cors_allow_origins` por settings);
  jamás `*` ni toda la API.
- **Hotel multi-día**: posible extensión del pack_agenda; no bloquear el resto.
- **Demos públicas**: rol `vendedor`, rate-limit, reset nocturno; nunca datos reales en demos.
- **Punto Rojo intacto**: el de-branding no toca el branding por-tenant de PR (su rojo `#C8200E`
  pasa de "default de plataforma" a "branding de PR" explícito — migración chica de control DB).
- **Railway wildcard**: confirmar soporte/costo de `*.melquiadez.com` en el plan actual de Railway; si no,
  agregar subdominios uno a uno al provisionar (peor, pero funciona).

## Checklist

Marcado al cierre (12 jun 2026). `[x]` = código/artefacto en `main`; las notas señalan el residuo de
infra/ops externo (no es código, no se puede mezclar).

- [x] M1 — Logo (sello + wordmark en `landing/marca/`, favicon en dashboard) + tokens + de-branding FerreBot→Melquiadez.
- [x] M2 — `BASE_DOMAIN` (settings) + **slugs reservados** (resolver + schema + tests). ⏳ *ops:* DNS wildcard en Cloudflare + custom domains en Railway.
- [x] M3 — Landing React (Vite+Tailwind, hero rotante, acordeón de verticales, `/`, `/login`, `/demo`).
- [x] M4 — `/login` en la landing + **CORS quirúrgico** (`apps/api/cors.py`) + handoff por fragmento (`dashboard/src/lib/handoff.js`).
- [x] M5 — 4 manifiestos demo + identidades demo + reset nocturno (`seed_demo_transaccional` + cron `resembrar_demos`). ⏳ *ops:* provisionarlas en prod (`railway ssh`, ver `docs/onboarding-tenant.md`).
- [x] M6 — Presets de branding por vertical (`core/tenancy/branding_presets.py`) + `GET /config` con tokens resueltos.
- [x] M7 — `tools/switch_demo.py` (idempotente, limpia memoria Redis). ⏳ *ops:* rebrand del avatar/nombre del número en Kapso (marca Melquiadez).
- [x] M8 — Onboarding actualizado (`docs/onboarding-tenant.md`) + smoke E2E (`tests/test_e2e_superficie_publica.py`) + harness visual (`dashboard/visual-harness/`, `landing/marca/harness.html`).
