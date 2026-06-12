# Landing pública — melquiadez.com

SPA en **Vite + React + Tailwind + shadcn/ui** (mismo stack del dashboard). Rutas: `/` (landing),
`/login` (sign-in contra la API real), `/demo` (selector de demos en vivo).

```bash
npm install
npm run dev        # http://localhost:5173
npm test           # vitest (lógica de rotación, retematización, tema y login)
npm run build      # → dist/ (lo sirve Cloudflare via ../wrangler.jsonc)
```

## Cómo está armada

- **Marca**: tokens centrales en `marca/tokens.css` (papel / tinta noche / oro viejo + acentos
  por vertical); logo en `marca/` (sello M, wordmark Fraunces, lockup). Ver `marca/README.md`.
- **Retematización**: `data-vertical` en `<html>` cambia `--acento` en toda la página
  (`src/lib/verticales.js`); el teléfono del hero reproduce la conversación del vertical activo.
- **Tema claro/oscuro**: `data-tema` en `<html>`; primer paint sin flash (script inline en
  `index.html`), persiste en localStorage. `?tema=claro|oscuro` fuerza el tema (QA).
- **Componentes 21st.dev**: `text-rotate` (palabra del titular), `interactive-image-accordion`
  (rework en `AcordeonVerticales.jsx`), `blur-fade`, `container-scroll-animation` (sección
  dashboard), `split-login-card` (rework en `pages/Login.jsx`). El shader del fondo es
  `aurora-flow` retintado a oro/tinta y portado a WebGL puro (`AuroraOro.jsx`, ~5KB de chunk
  en vez de three.js completo).
- **Login**: `POST {VITE_API_URL}/api/v1/auth/login/password`; al éxito redirige a
  `{VITE_APP_URL}/#token=…` (fragmento: no viaja al servidor). 401 → mensaje genérico,
  429 → bloqueo temporal. Requiere CORS de `/auth/*` para el origin de la landing (rama backend).
- **Demos**: linkean a `{slug}-demo.melquiadez.com` (tenants demo del plan, §4–5).

## Deploy

`wrangler.jsonc` (raíz del repo) apunta a `landing/dist` como assets estáticos de Cloudflare
con fallback SPA. `npm run build` y `wrangler deploy`.
