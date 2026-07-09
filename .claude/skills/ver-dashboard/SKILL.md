---
name: ver-dashboard
description: Ver o depurar el dashboard de un tenant en Chromium headless (screenshot, DOM, estilos computados), local o de PRODUCCIÓN sin contraseña vía dev_token + handoff #token=. Usar cuando el usuario diga "muéstrame el dashboard de <tenant>", "screenshot de <página>", "este tab se ve mal en prod", o pida depurar UI de un tenant.
---

# /ver-dashboard <tenant> [prod] — captura y diagnóstico headless

Guarda TODOS los artefactos (png, html, logs) en el **scratchpad de la sesión**, nunca en el repo.

## 1. Token

- **Local**: `.venv/Scripts/python.exe -m tools.dev_token <slug>`
- **Prod** (firma con el secreto JWT de prod, no requiere contraseña): `railway ssh python -m tools.dev_token <slug>`

El dashboard rehidrata sesión desde `#token=` en la URL (handoff en `dashboard/src/main.jsx`).

## 2. Binarios de Chromium (Windows)

- Desktop: `~/AppData/Local/ms-playwright/chromium-1228/chrome-win64/chrome.exe` (playwright NO está como paquete, el binario sí).
- **Anchos móviles (360/375/390)**: usar `~/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe` — el chrome completo pisa el ancho a ~482px y el screenshot recortado simula un overflow que NO existe.
- En Git Bash, prefijar con `MSYS_NO_PATHCONV=1` para que no mangle rutas.

## 3. Capturas

```bash
# Screenshot desktop de la página real:
MSYS_NO_PATHCONV=1 "<chrome>" --headless --no-sandbox --window-size=1600,900 \
  --virtual-time-budget=12000 --screenshot="<scratchpad>/out.png" \
  "https://<slug>.melquiadez.com/<ruta>#token=<JWT>"

# DOM real (pasar --window-size igual; sin él sale layout móvil):
... --dump-dom "..." > "<scratchpad>/dom.html"
```

Local: misma receta contra `http://localhost:5173/<ruta>#token=<JWT>`.

## 4. Diagnóstico de layout (sin CDP)

Para anchos/flex reales: inyectar un `<script>` que en `load`+setTimeout lea `getComputedStyle`/`getBoundingClientRect` y escriba a un `<div id="DIAG">`; luego `--dump-dom` y grep `DIAG=`. Overflow móvil real: comparar `document.documentElement.scrollWidth` vs `window.innerWidth` (iguales = sin overflow).

Causas típicas en este repo (ver memoria `repro-dashboard-prod-headless`):
- Grids `grid gap-N md:grid-cols-X` **sin `grid-cols-1` base** → columnas implícitas que desbordan en móvil.
- Dos utilidades Tailwind de la misma propiedad: gana la última del CSS compilado, no el orden del className — overridear con `CLS.replace('w-full','w-36')`.

## 5. Probar TU código con datos de prod (sin desplegar)

`VITE_TENANT_SLUG=<slug> npm run dev` + apuntar TEMPORALMENTE el proxy `/api/v1` de `dashboard/vite.config.js` a `https://<slug>.melquiadez.com` (`changeOrigin: true`) + dev_token de prod por `#token=`. **Revertir el proxy antes de commitear.**

## 6. Entrega

Mostrar el screenshot al usuario (Read del png) y describir qué se ve; si es un bug, señalar el nodo/clase culpable con evidencia del DIAG.
